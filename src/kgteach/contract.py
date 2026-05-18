"""Stable JSON envelope and error codes for agent-facing commands."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "0.1.0"


class ErrorCode(StrEnum):
    """Machine-readable command error codes."""

    INVALID_INPUT = "INVALID_INPUT"
    ENGINE_UNAVAILABLE = "ENGINE_UNAVAILABLE"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    CONFIG_NOT_FOUND = "CONFIG_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    KATAGO_PROTOCOL_ERROR = "KATAGO_PROTOCOL_ERROR"
    SGF_PARSE_ERROR = "SGF_PARSE_ERROR"
    ILLEGAL_MOVE = "ILLEGAL_MOVE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


WarningPayload = dict[str, Any]
Envelope = dict[str, Any]


def envelope_ok(
    *,
    command: str,
    data: dict[str, Any],
    warnings: list[WarningPayload] | None = None,
    debug: dict[str, Any] | None = None,
) -> Envelope:
    """Build a success envelope with the stable public shape."""

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "data": data,
        "warnings": [] if warnings is None else warnings,
        "debug": debug,
    }


def envelope_error(
    *,
    command: str,
    code: ErrorCode,
    message: str,
    warnings: list[WarningPayload] | None = None,
    partial: dict[str, Any] | None = None,
    **details: Any,
) -> Envelope:
    """Build an error envelope that remains parseable by agents."""

    error: dict[str, Any] = {
        "code": code.value,
        "message": message,
    }
    error.update(details)
    return {
        "ok": False,
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "error": error,
        "warnings": [] if warnings is None else warnings,
        "partial": partial,
    }
