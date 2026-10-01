"""Local daemon lifecycle primitives for kgteach."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from kgteach.contract import ErrorCode
from kgteach.engine_runtime import (
    AnalysisRuntime,
    _unlink_quietly,
    cache_file_path,
    cache_files,
    cache_key,
    disk_cache_keys,
    is_cache_key,
    read_cache_file,
    write_cache_file,
)
from kgteach.engine_runtime import (
    _file_identity as _file_identity,
)
from kgteach.katago import KataGoProtocolError, KataGoTimeoutError
from kgteach.runtime import KataGoRuntimeError, close_process
from kgteach.runtime import _close_stdin as _close_stdin

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
    runtime = AnalysisRuntime(cache_dir=cache_dir_path(state_file.parent))
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


# Keep private names as compatibility aliases for one release.
_DaemonRuntime = AnalysisRuntime
_cache_key = cache_key
_cache_file_path = cache_file_path
_read_cache_file = read_cache_file
_write_cache_file = write_cache_file
_disk_cache_keys = disk_cache_keys
_cache_files = cache_files
_remove_cache_file = _unlink_quietly
_is_cache_key = is_cache_key
_close_process = close_process


async def _handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    runtime: AnalysisRuntime,
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
    runtime: AnalysisRuntime,
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
    runtime: AnalysisRuntime,
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


def _json_line(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        + "\n"
    ).encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
