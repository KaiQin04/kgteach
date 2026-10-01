"""Runtime helpers for launching KataGo analysis."""

from __future__ import annotations

import asyncio
import inspect
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kgteach.config import KataGoConfig
from kgteach.contract import ErrorCode
from kgteach.katago import (
    KataGoEngineClient,
    KataGoProtocolError,
    KataGoTimeoutError,
)


class RuntimeUnavailableError(RuntimeError):
    """Raised when KataGo cannot be launched from the resolved config."""


class KataGoRuntimeError(RuntimeError):
    """Raised when KataGo runtime setup, launch, or analysis fails."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = {} if details is None else dict(details)


def runtime_ready(config: KataGoConfig) -> bool:
    """Return whether config has enough information to launch KataGo."""

    return bool(config.binary and config.model and config.config)


def check_runtime_ready(config: KataGoConfig) -> dict[str, Any]:
    """Return a controller-friendly readiness report for the resolved runtime."""

    katago_found = _binary_found(config.binary)
    model_found = _path_found(config.model)
    config_found = _path_found(config.config)
    human_model_found = bool(config.human_model and _path_found(config.human_model))
    errors: list[dict[str, Any]] = []

    if not katago_found:
        errors.append(
            {
                "code": ErrorCode.ENGINE_UNAVAILABLE.value,
                "message": "KataGo binary was not found.",
                "path": config.binary,
            }
        )
    if not config.model or not model_found:
        errors.append(
            {
                "code": ErrorCode.MODEL_NOT_FOUND.value,
                "message": "KataGo model file was not found.",
                "path": config.model,
            }
        )
    if not config.config or not config_found:
        errors.append(
            {
                "code": ErrorCode.CONFIG_NOT_FOUND.value,
                "message": "KataGo analysis config file was not found.",
                "path": config.config,
            }
        )

    ready = katago_found and model_found and config_found
    command: list[str] | None = None
    if ready:
        command = build_analysis_command(config)

    return {
        "ready": ready,
        "katago_found": katago_found,
        "model_found": model_found,
        "config_found": config_found,
        "human_model_found": human_model_found,
        "command": command,
        "capabilities": {
            "analyze_game": ready,
            "ownership": ready,
            "human_policy": human_model_found,
            "batch_turns": True,
        },
        "errors": errors,
    }


def build_analysis_command(config: KataGoConfig) -> list[str]:
    """Build the argv list for KataGo analysis mode."""

    if not config.model:
        raise KataGoRuntimeError(
            ErrorCode.MODEL_NOT_FOUND,
            "KataGo model path is required.",
        )
    if not config.config:
        raise KataGoRuntimeError(
            ErrorCode.CONFIG_NOT_FOUND,
            "KataGo analysis config path is required.",
        )

    args = [
        _argv_path(config.binary),
        "analysis",
        "-config",
        _argv_path(config.config),
        "-model",
        _argv_path(config.model),
    ]
    if config.human_model:
        args.extend(["-human-model", _argv_path(config.human_model)])
    return args


def launch_args(config: KataGoConfig) -> list[str]:
    """Build the argv list for KataGo analysis mode."""

    return build_analysis_command(config)


async def analyze_query(
    query: Mapping[str, Any],
    config: KataGoConfig,
    timeout: float | None = 30.0,
) -> list[dict[str, Any]]:
    """Launch KataGo, run one query, and return final responses."""

    process = await _create_analysis_process(config)
    client = KataGoEngineClient(process)
    try:
        return await client.analyze(dict(query), timeout=timeout)
    except KataGoTimeoutError as exc:
        request_id = query.get("id", "unknown")
        raise KataGoRuntimeError(
            ErrorCode.TIMEOUT,
            f"KataGo analysis timed out: {request_id}",
            details={"request_id": request_id},
        ) from exc
    except KataGoProtocolError as exc:
        raise KataGoRuntimeError(
            ErrorCode.KATAGO_PROTOCOL_ERROR,
            str(exc),
        ) from exc
    finally:
        await client.aclose()
        await _close_process(process)


async def query_version(
    *,
    config: KataGoConfig,
    timeout: float | None = 10.0,
) -> dict[str, Any]:
    """Launch KataGo and query version metadata."""

    process = await _create_analysis_process(config)
    client = KataGoEngineClient(process)
    try:
        return await client.query_version(timeout=timeout)
    except KataGoTimeoutError as exc:
        raise KataGoRuntimeError(
            ErrorCode.TIMEOUT,
            "KataGo version query timed out.",
        ) from exc
    except KataGoProtocolError as exc:
        raise KataGoRuntimeError(
            ErrorCode.KATAGO_PROTOCOL_ERROR,
            str(exc),
        ) from exc
    finally:
        await client.aclose()
        await _close_process(process)


def analyze_query_sync(
    query: Mapping[str, Any],
    config: KataGoConfig,
    timeout: float | None = 30.0,
) -> list[dict[str, Any]]:
    """Synchronous wrapper for CLI use."""

    return asyncio.run(analyze_query(query, config, timeout=timeout))


def query_version_sync(
    *,
    config: KataGoConfig,
    timeout: float | None = 10.0,
) -> dict[str, Any]:
    """Synchronous version query wrapper."""

    return asyncio.run(query_version(config=config, timeout=timeout))


async def _create_analysis_process(config: KataGoConfig) -> Any:
    argv = build_analysis_command(config)
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise KataGoRuntimeError(
            ErrorCode.ENGINE_UNAVAILABLE,
            f"KataGo binary was not found: {config.binary}",
            details={"binary": config.binary},
        ) from exc
    except OSError as exc:
        raise KataGoRuntimeError(
            ErrorCode.ENGINE_UNAVAILABLE,
            f"KataGo process could not be launched: {exc}",
            details={"binary": config.binary},
        ) from exc


async def close_process(process: Any, *, timeout: float = 2.0) -> None:
    """Close stdin, terminate, then kill the KataGo process after timeout."""

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


_close_process = close_process


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


def _binary_found(binary: str) -> bool:
    return shutil.which(binary) is not None or _path_found(binary)


def _path_found(path: str | None) -> bool:
    if not path:
        return False
    return Path(path).expanduser().exists()


def _argv_path(path: str) -> str:
    return str(Path(path).expanduser())
