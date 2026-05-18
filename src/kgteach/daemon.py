"""Local daemon lifecycle primitives for kgteach."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from kgteach.config import KataGoConfig, resolve_katago_config
from kgteach.contract import ErrorCode
from kgteach.katago import KataGoEngineClient, KataGoProtocolError, KataGoTimeoutError
from kgteach.runtime import KataGoRuntimeError, build_analysis_command

STATE_FILENAME = "daemon.json"
SOCKET_FILENAME = "daemon.sock"
CACHE_DIRNAME = "cache"
STATE_KIND = "kgteach.daemon"
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 0.25
STALE_HEARTBEAT_SECONDS = 10.0
START_TIMEOUT_SECONDS = 5.0
STOP_TIMEOUT_SECONDS = 5.0

DaemonStatus = dict[str, object]


class DaemonError(RuntimeError):
    """Raised when daemon lifecycle management cannot complete."""


def default_state_dir() -> Path:
    """Return the default user-facing state directory."""

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base is not None:
            return Path(base).expanduser() / "kgteach"
        return Path.home() / "AppData" / "Local" / "kgteach"
    return Path.home() / ".kgteach"


def state_file_path(state_dir: str | Path | None = None) -> Path:
    """Return the daemon state file path for a state directory."""

    return _resolve_state_dir(state_dir) / STATE_FILENAME


def socket_file_path(state_dir: str | Path | None = None) -> Path:
    """Return the daemon Unix socket path for a state directory."""

    candidate = _resolve_state_dir(state_dir) / SOCKET_FILENAME
    if os.name != "nt" and len(str(candidate)) > 100:
        digest = hashlib.sha256(str(candidate).encode("utf-8")).hexdigest()[:16]
        return Path("/tmp") / f"kgteach-{digest}.sock"
    return candidate


def cache_dir_path(state_dir: str | Path | None = None) -> Path:
    """Return the persistent daemon cache directory path."""

    return _resolve_state_dir(state_dir) / CACHE_DIRNAME


def status(state_dir: str | Path | None = None) -> DaemonStatus:
    """Return daemon status and clean stale state if needed."""

    state_file = state_file_path(state_dir)
    state = _read_state(state_file)
    if state is None:
        return _stopped_status()

    pid = _optional_int(state.get("pid"))
    if pid is None or not _process_exists(pid) or _state_is_stale(state):
        _remove_state_file(state_file)
        return _stopped_status()

    started_at = _optional_float(state.get("started_at")) or time.time()
    katago_pid = _optional_int(state.get("katago_pid"))
    if katago_pid is not None and not _process_exists(katago_pid):
        katago_pid = None

    return {
        "running": True,
        "pid": pid,
        "katago_pid": katago_pid,
        "socket_path": str(socket_file_path(state_file.parent)),
        "uptime_seconds": max(0, int(time.time() - started_at)),
        "active_requests": _non_negative_int(state.get("active_requests")),
        "queued_requests": _non_negative_int(state.get("queued_requests")),
        "cache_items": _non_negative_int(state.get("cache_items")),
    }


def status_daemon(state_dir: str | Path | None = None) -> DaemonStatus:
    """Backward-compatible alias used by the CLI layer."""

    return status(state_dir)


def start_daemon(
    state_dir: str | Path | None = None,
    *,
    python_executable: str | None = None,
    timeout: float = START_TIMEOUT_SECONDS,
) -> DaemonStatus:
    """Start the daemon process unless it is already running."""

    current_status = status(state_dir)
    if current_status["running"] is True:
        return current_status

    resolved_state_dir = _resolve_state_dir(state_dir)
    resolved_state_dir.mkdir(parents=True, exist_ok=True)
    executable = python_executable or sys.executable
    command = [
        executable,
        "-m",
        "kgteach.daemon",
        "--serve",
        "--state-dir",
        str(resolved_state_dir),
    ]
    process = _spawn_daemon(command)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise DaemonError("Daemon process exited before writing state.")
        current_status = status(resolved_state_dir)
        if (
            current_status["running"] is True
            and current_status["pid"] == process.pid
        ):
            return current_status
        time.sleep(0.05)

    _terminate_pid(process.pid)
    _wait_for_exit(process.pid, timeout=1.0)
    _remove_state_if_pid(state_file_path(resolved_state_dir), process.pid)
    raise DaemonError("Timed out waiting for daemon process to start.")


def stop_daemon(
    state_dir: str | Path | None = None,
    *,
    timeout: float = STOP_TIMEOUT_SECONDS,
) -> DaemonStatus:
    """Terminate the daemon process and clean its state file."""

    state_file = state_file_path(state_dir)
    current_status = status(state_dir)
    if current_status["running"] is not True:
        _remove_state_file(state_file)
        return _stopped_status()

    pid = _optional_int(current_status.get("pid"))
    if pid is None:
        _remove_state_file(state_file)
        return _stopped_status()

    _terminate_pid(pid)
    if not _wait_for_exit(pid, timeout=timeout):
        _kill_pid(pid)
        if not _wait_for_exit(pid, timeout=1.0):
            message = f"Timed out waiting for daemon pid {pid} to stop."
            raise DaemonError(message)

    _remove_state_if_pid(state_file, pid)
    _remove_socket_file(socket_file_path(state_dir))
    return _stopped_status()


def request_daemon(
    payload: Mapping[str, Any],
    state_dir: str | Path | None = None,
    *,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send one JSON request to a running local daemon."""

    return asyncio.run(_request_daemon(payload, state_dir, timeout=timeout))


def serve(
    state_dir: str | Path | None = None,
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
) -> int:
    """Run the daemon socket server until SIGTERM or SIGINT."""

    return asyncio.run(
        _serve_async(
            state_dir,
            heartbeat_interval=heartbeat_interval,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the daemon module command-line entrypoint."""

    parser = argparse.ArgumentParser(prog="python -m kgteach.daemon")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    )
    args = parser.parse_args(argv)
    if not args.serve:
        parser.error("--serve is required")
    return serve(
        args.state_dir,
        heartbeat_interval=args.heartbeat_interval,
    )


async def _request_daemon(
    payload: Mapping[str, Any],
    state_dir: str | Path | None,
    *,
    timeout: float,
) -> dict[str, Any]:
    socket_path = socket_file_path(state_dir)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(socket_path)),
            timeout=timeout,
        )
    except (FileNotFoundError, ConnectionError, TimeoutError) as exc:
        raise DaemonError(f"Could not connect to daemon socket: {socket_path}") from exc

    try:
        writer.write(_json_line(dict(payload)))
        await writer.drain()
        raw_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
    finally:
        writer.close()
        await writer.wait_closed()

    if not raw_line:
        raise DaemonError("Daemon closed the socket without a response.")
    response = json.loads(raw_line.decode("utf-8"))
    if not isinstance(response, dict):
        raise DaemonError("Daemon returned a non-object response.")
    return response


async def _serve_async(
    state_dir: str | Path | None = None,
    *,
    heartbeat_interval: float,
) -> int:
    state_file = state_file_path(state_dir)
    socket_file = socket_file_path(state_dir)
    current_status = status(state_file.parent)
    own_pid = os.getpid()
    if current_status["running"] is True and current_status["pid"] != own_pid:
        return 0

    state_file.parent.mkdir(parents=True, exist_ok=True)
    _remove_socket_file(socket_file)
    stop_event = asyncio.Event()
    runtime = _DaemonRuntime(cache_dir=cache_dir_path(state_file.parent))
    metrics = _DaemonMetrics(started_at=time.time())

    server = await asyncio.start_unix_server(
        lambda reader, writer: _handle_client(reader, writer, runtime, metrics, state_file),
        path=str(socket_file),
    )
    _install_async_signal_handlers(stop_event)
    heartbeat = asyncio.create_task(
        _heartbeat_loop(
            state_file,
            metrics,
            runtime,
            heartbeat_interval=heartbeat_interval,
        )
    )

    try:
        await stop_event.wait()
    finally:
        server.close()
        await server.wait_closed()
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        await runtime.close()
        _remove_socket_file(socket_file)
        _remove_state_if_pid(state_file, own_pid)
    return 0


class _DaemonMetrics:
    """Mutable daemon metrics exposed in status output."""

    def __init__(self, *, started_at: float) -> None:
        self.started_at = started_at
        self.active_requests = 0
        self.queued_requests = 0


class _DaemonRuntime:
    """Long-lived KataGo analysis runtime plus persistent response cache."""

    def __init__(self, *, cache_dir: Path) -> None:
        self._process: Any | None = None
        self._client: KataGoEngineClient | None = None
        self._config: KataGoConfig | None = None
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_dir = cache_dir
        self._lock = asyncio.Lock()

    @property
    def cache_items(self) -> int:
        """Return the number of cached analysis results."""

        return len(set(self._cache) | _disk_cache_keys(self._cache_dir))

    def cache_summary(self) -> dict[str, Any]:
        """Return cache metrics for daemon status responses."""

        return {
            "cache_items": self.cache_items,
            "memory_items": len(self._cache),
            "disk_items": len(_disk_cache_keys(self._cache_dir)),
            "persistent": True,
            "cache_dir": str(self._cache_dir),
        }

    @property
    def katago_pid(self) -> int | None:
        """Return the child KataGo process id when available."""

        process = self._process
        pid = getattr(process, "pid", None)
        return pid if isinstance(pid, int) else None

    async def analyze(
        self,
        query: Mapping[str, Any],
        *,
        timeout: float | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Analyze with a persistent KataGo process and cache by query/config."""

        config = resolve_katago_config()
        cache_key = _cache_key(query, config)
        if cache_key in self._cache:
            return [dict(item) for item in self._cache[cache_key]], True
        disk_responses = _read_cache_file(self._cache_dir, cache_key)
        if disk_responses is not None:
            self._cache[cache_key] = [dict(item) for item in disk_responses]
            return [dict(item) for item in disk_responses], True

        async with self._lock:
            if cache_key in self._cache:
                return [dict(item) for item in self._cache[cache_key]], True
            disk_responses = _read_cache_file(self._cache_dir, cache_key)
            if disk_responses is not None:
                self._cache[cache_key] = [dict(item) for item in disk_responses]
                return [dict(item) for item in disk_responses], True
            client = await self._client_for_config(config)
            responses = await client.analyze(query, timeout=timeout)
            self._cache[cache_key] = [dict(item) for item in responses]
            _write_cache_file(self._cache_dir, cache_key, responses)
            return responses, False

    def clear_cache(self) -> int:
        """Clear memory and disk cache entries, returning removed item count."""

        previous_keys = set(self._cache) | _disk_cache_keys(self._cache_dir)
        self._cache.clear()
        for cache_file in _cache_files(self._cache_dir):
            try:
                cache_file.unlink()
            except FileNotFoundError:
                continue
        return len(previous_keys)

    async def close(self) -> None:
        """Close the persistent KataGo process if it is running."""

        client = self._client
        process = self._process
        self._client = None
        self._process = None
        self._config = None
        if client is not None:
            await client.aclose()
        if process is not None:
            await _close_process(process)

    async def _client_for_config(self, config: KataGoConfig) -> KataGoEngineClient:
        if self._client is not None and self._config == config:
            return self._client
        await self.close()
        command = build_analysis_command(config)
        self._process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._client = KataGoEngineClient(self._process)
        self._config = config
        return self._client


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    runtime: _DaemonRuntime,
    metrics: _DaemonMetrics,
    state_file: Path,
) -> None:
    metrics.active_requests += 1
    try:
        raw_line = await reader.readline()
        if not raw_line:
            return
        try:
            request = json.loads(raw_line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            response = await _route_request(request, runtime)
        except Exception as exc:  # pragma: no cover - daemon boundary
            response = _daemon_error_response(exc)
        writer.write(_json_line(response))
        await writer.drain()
    finally:
        metrics.active_requests -= 1
        _write_state(
            state_file,
            pid=os.getpid(),
            started_at=metrics.started_at,
            updated_at=time.time(),
            active_requests=metrics.active_requests,
            queued_requests=metrics.queued_requests,
            cache_items=runtime.cache_items,
            katago_pid=runtime.katago_pid,
        )
        writer.close()
        await writer.wait_closed()


async def _route_request(
    request: Mapping[str, Any],
    runtime: _DaemonRuntime,
) -> dict[str, Any]:
    action = str(request.get("action", "ping"))
    if action == "ping":
        ping_data: dict[str, Any] = {"pong": True}
        ping_data.update(runtime.cache_summary())
        return {"ok": True, "data": ping_data}
    if action == "cache_status":
        return {"ok": True, "data": runtime.cache_summary()}
    if action in {"clear_cache", "cache_clear"}:
        removed_items = runtime.clear_cache()
        clear_data: dict[str, Any] = {"removed_items": removed_items}
        clear_data.update(runtime.cache_summary())
        return {"ok": True, "data": clear_data}
    if action == "analyze":
        query = request.get("query")
        if not isinstance(query, Mapping):
            return _daemon_error_payload(ErrorCode.INVALID_INPUT, "analyze requires query object")
        timeout = _optional_float(request.get("timeout"))
        responses, cache_hit = await runtime.analyze(query, timeout=timeout)
        return {
            "ok": True,
            "data": {
                "responses": responses,
                "cache_hit": cache_hit,
                "cache_items": runtime.cache_items,
            },
        }
    return _daemon_error_payload(ErrorCode.INVALID_INPUT, f"Unknown daemon action: {action}")


async def _heartbeat_loop(
    state_file: Path,
    metrics: _DaemonMetrics,
    runtime: _DaemonRuntime,
    *,
    heartbeat_interval: float,
) -> None:
    while True:
        _write_state(
            state_file,
            pid=os.getpid(),
            started_at=metrics.started_at,
            updated_at=time.time(),
            active_requests=metrics.active_requests,
            queued_requests=metrics.queued_requests,
            cache_items=runtime.cache_items,
            katago_pid=runtime.katago_pid,
        )
        await asyncio.sleep(max(0.05, heartbeat_interval))


def _resolve_state_dir(state_dir: str | Path | None) -> Path:
    if state_dir is None:
        return default_state_dir().expanduser()
    return Path(state_dir).expanduser()


def _stopped_status() -> DaemonStatus:
    return {
        "running": False,
        "pid": None,
        "katago_pid": None,
        "uptime_seconds": 0,
        "active_requests": 0,
        "queued_requests": 0,
        "cache_items": 0,
    }


def _spawn_daemon(command: list[str]) -> subprocess.Popen[bytes]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _read_state(state_file: Path) -> dict[str, Any] | None:
    try:
        raw_state = json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        _remove_state_file(state_file)
        return None
    if not isinstance(raw_state, dict):
        _remove_state_file(state_file)
        return None
    return raw_state


def _write_state(
    state_file: Path,
    *,
    pid: int,
    started_at: float,
    updated_at: float,
    active_requests: int = 0,
    queued_requests: int = 0,
    cache_items: int = 0,
    katago_pid: int | None = None,
) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kind": STATE_KIND,
        "pid": pid,
        "katago_pid": katago_pid,
        "started_at": started_at,
        "updated_at": updated_at,
        "uptime_seconds": max(0, int(updated_at - started_at)),
        "active_requests": active_requests,
        "queued_requests": queued_requests,
        "cache_items": cache_items,
    }
    temporary_file = state_file.with_name(f"{state_file.name}.tmp")
    temporary_file.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary_file, state_file)


def _remove_state_file(state_file: Path) -> None:
    try:
        state_file.unlink()
    except FileNotFoundError:
        return


def _remove_socket_file(socket_file: Path) -> None:
    try:
        socket_file.unlink()
    except FileNotFoundError:
        return


def _remove_state_if_pid(state_file: Path, pid: int) -> None:
    state = _read_state(state_file)
    if state is None:
        return
    if _optional_int(state.get("pid")) == pid:
        _remove_state_file(state_file)


def _state_is_stale(state: dict[str, Any]) -> bool:
    if state.get("kind") != STATE_KIND:
        return True
    updated_at = _optional_float(state.get("updated_at"))
    if updated_at is None:
        return True
    return time.time() - updated_at > STALE_HEARTBEAT_SECONDS


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _terminate_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _kill_pid(pid: int) -> None:
    kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        os.kill(pid, kill_signal)
    except ProcessLookupError:
        return


def _wait_for_exit(pid: int, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _reap_child(pid):
            return True
        if not _process_exists(pid):
            return True
        time.sleep(0.05)
    return _reap_child(pid) or not _process_exists(pid)


def _reap_child(pid: int) -> bool:
    wait_no_hang = getattr(os, "WNOHANG", None)
    if wait_no_hang is None:
        return False
    try:
        waited_pid, _status = os.waitpid(pid, wait_no_hang)
    except ChildProcessError:
        return False
    except OSError:
        return False
    return waited_pid == pid


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _non_negative_int(value: object) -> int:
    number = _optional_int(value)
    if number is None:
        return 0
    return max(0, number)


def _install_async_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


async def _close_process(process: Any, *, timeout: float = 2.0) -> None:
    await _close_stdin(process)
    if getattr(process, "returncode", None) is not None:
        return
    terminate = getattr(process, "terminate", None)
    if terminate is not None:
        try:
            terminate()
        except ProcessLookupError:
            return
    wait = getattr(process, "wait", None)
    if wait is None:
        return
    try:
        await asyncio.wait_for(wait(), timeout=timeout)
    except TimeoutError:
        kill = getattr(process, "kill", None)
        if kill is not None:
            try:
                kill()
            except ProcessLookupError:
                return
        await wait()


async def _close_stdin(process: Any) -> None:
    stdin = getattr(process, "stdin", None)
    if stdin is None:
        return
    close = getattr(stdin, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result
    wait_closed = getattr(stdin, "wait_closed", None)
    if wait_closed is not None:
        result = wait_closed()
        if inspect.isawaitable(result):
            await result


def _daemon_error_response(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, KataGoRuntimeError):
        return _daemon_error_payload(exc.code, str(exc))
    if isinstance(exc, KataGoTimeoutError):
        return _daemon_error_payload(ErrorCode.TIMEOUT, str(exc))
    if isinstance(exc, KataGoProtocolError):
        return _daemon_error_payload(ErrorCode.KATAGO_PROTOCOL_ERROR, str(exc))
    if isinstance(exc, FileNotFoundError | OSError):
        return _daemon_error_payload(ErrorCode.ENGINE_UNAVAILABLE, str(exc))
    return _daemon_error_payload(ErrorCode.INTERNAL_ERROR, str(exc))


def _daemon_error_payload(code: ErrorCode, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code.value, "message": message}}


def _cache_key(query: Mapping[str, Any], config: KataGoConfig) -> str:
    payload = {
        "query": query,
        "config": {
            "binary": _file_identity(config.binary),
            "model": _file_identity(config.model),
            "config": _file_identity(config.config),
            "human_model": _file_identity(config.human_model),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _cache_file_path(cache_dir: Path, cache_key: str) -> Path:
    if not _is_cache_key(cache_key):
        raise ValueError("cache key must be a sha256 hex digest")
    return cache_dir / f"{cache_key}.json"


def _read_cache_file(cache_dir: Path, cache_key: str) -> list[dict[str, Any]] | None:
    cache_file = _cache_file_path(cache_dir, cache_key)
    try:
        raw_payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        _remove_cache_file(cache_file)
        return None
    if not isinstance(raw_payload, dict):
        _remove_cache_file(cache_file)
        return None
    responses = raw_payload.get("responses")
    if not isinstance(responses, list) or not all(isinstance(item, dict) for item in responses):
        _remove_cache_file(cache_file)
        return None
    return [dict(item) for item in responses]


def _write_cache_file(
    cache_dir: Path,
    cache_key: str,
    responses: Sequence[Mapping[str, Any]],
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_file_path(cache_dir, cache_key)
    temporary_file = cache_file.with_name(f"{cache_file.name}.tmp")
    payload = {
        "kind": "kgteach.analysis_cache",
        "schema_version": "0.1.0",
        "responses": [dict(response) for response in responses],
    }
    temporary_file.write_text(
        json.dumps(payload, sort_keys=True, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(temporary_file, cache_file)


def _disk_cache_keys(cache_dir: Path) -> set[str]:
    return {cache_file.stem for cache_file in _cache_files(cache_dir)}


def _cache_files(cache_dir: Path) -> list[Path]:
    try:
        return [
            path
            for path in cache_dir.glob("*.json")
            if path.is_file() and _is_cache_key(path.stem)
        ]
    except OSError:
        return []


def _remove_cache_file(cache_file: Path) -> None:
    try:
        cache_file.unlink()
    except FileNotFoundError:
        return


def _is_cache_key(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _file_identity(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    expanded = Path(path).expanduser()
    try:
        stat = expanded.stat()
    except OSError:
        return {"path": str(expanded), "exists": False}
    return {
        "path": str(expanded),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _json_line(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
