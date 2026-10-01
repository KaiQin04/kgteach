"""Minimal stdio MCP server exposing kgteach CLI tools."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

SERVER_NAME = "kgteach"
SERVER_VERSION = "0.2.0"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

JsonObject = dict[str, Any]
ToolHandler = Callable[[Mapping[str, Any]], JsonObject]


def main() -> int:
    """Run the kgteach MCP server over stdin/stdout JSON-RPC lines."""

    return serve(sys.stdin, sys.stdout)


def serve(input_stream: TextIO, output_stream: TextIO) -> int:
    """Serve MCP JSON-RPC requests from an input stream."""

    for raw_line in input_stream:
        if not raw_line.strip():
            continue
        response = handle_json_line(raw_line)
        if response is None:
            continue
        output_stream.write(json.dumps(response, separators=(",", ":"), allow_nan=False))
        output_stream.write("\n")
        output_stream.flush()
    return 0


def handle_json_line(raw_line: str) -> JsonObject | None:
    """Handle one JSON-RPC line and return a response payload when needed."""

    try:
        request = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        return _error_response(None, -32700, f"Parse error: {exc.msg}")
    if not isinstance(request, dict):
        return _error_response(None, -32600, "Request must be a JSON object.")

    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str):
        return _error_response(request_id, -32600, "Request method must be a string.")

    if request_id is None:
        return None

    try:
        result = _dispatch(method, request.get("params"))
    except ValueError as exc:
        return _error_response(request_id, -32602, str(exc))
    except Exception as exc:  # pragma: no cover - defensive MCP boundary
        return _error_response(request_id, -32603, str(exc))
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _dispatch(method: str, params: object) -> JsonObject:
    if method == "initialize":
        protocol_version = DEFAULT_PROTOCOL_VERSION
        if isinstance(params, Mapping):
            requested_version = params.get("protocolVersion")
            if isinstance(requested_version, str) and requested_version:
                protocol_version = requested_version
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": _tool_definitions()}
    if method == "tools/call":
        if not isinstance(params, Mapping):
            raise ValueError("tools/call params must be an object.")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str):
            raise ValueError("tools/call params.name must be a string.")
        if not isinstance(arguments, Mapping):
            raise ValueError("tools/call params.arguments must be an object.")
        return _call_tool(name, arguments)
    raise ValueError(f"Unsupported MCP method: {method}")


def _call_tool(name: str, arguments: Mapping[str, Any]) -> JsonObject:
    handlers: dict[str, ToolHandler] = {
        "engine_health": _tool_engine_health,
        "daemon_status": _tool_daemon_status,
        "game_inspect": _tool_game_inspect,
        "teach_move": _tool_teach_move,
        "teach_compare": _tool_teach_compare,
        "teach_line": _tool_teach_line,
        "teach_rank_compare": _tool_teach_rank_compare,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValueError(f"Unknown kgteach tool: {name}")
    payload = handler(arguments)
    is_error = payload.get("ok") is False
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                ),
            }
        ],
        "isError": bool(is_error),
    }


def _tool_engine_health(_arguments: Mapping[str, Any]) -> JsonObject:
    return _run_kgteach(["engine", "health"])


def _tool_daemon_status(_arguments: Mapping[str, Any]) -> JsonObject:
    return _run_kgteach(["daemon", "status"])


def _tool_game_inspect(arguments: Mapping[str, Any]) -> JsonObject:
    return _run_kgteach(["game", "inspect", _required_str(arguments, "sgf_path")])


def _tool_teach_move(arguments: Mapping[str, Any]) -> JsonObject:
    args = [
        "teach",
        "move",
        _required_str(arguments, "sgf_path"),
        "--turn",
        str(_required_int(arguments, "turn")),
    ]
    _append_common_teach_options(args, arguments)
    return _run_kgteach(args)


def _tool_teach_compare(arguments: Mapping[str, Any]) -> JsonObject:
    args = [
        "teach",
        "compare",
        _required_str(arguments, "sgf_path"),
        "--turn",
        str(_required_int(arguments, "turn")),
        "--moves",
        _string_list_argument(arguments, "moves"),
    ]
    _append_visits_timeout(args, arguments)
    return _run_kgteach(args)


def _tool_teach_line(arguments: Mapping[str, Any]) -> JsonObject:
    args = [
        "teach",
        "line",
        _required_str(arguments, "sgf_path"),
        "--turn",
        str(_required_int(arguments, "turn")),
        "--line",
        _space_list_argument(arguments, "line"),
    ]
    _append_timeout(args, arguments)
    return _run_kgteach(args)


def _tool_teach_rank_compare(arguments: Mapping[str, Any]) -> JsonObject:
    args = [
        "teach",
        "rank-compare",
        _required_str(arguments, "sgf_path"),
        "--turn",
        str(_required_int(arguments, "turn")),
        "--ranks",
        _string_list_argument(arguments, "ranks"),
    ]
    _append_common_teach_options(args, arguments)
    return _run_kgteach(args)


def _append_common_teach_options(args: list[str], arguments: Mapping[str, Any]) -> None:
    student_rank = arguments.get("student_rank")
    if isinstance(student_rank, str) and student_rank.strip():
        args.extend(["--student-rank", student_rank.strip()])
    _append_visits_timeout(args, arguments)


def _append_visits_timeout(args: list[str], arguments: Mapping[str, Any]) -> None:
    visits = arguments.get("visits")
    if visits is not None:
        args.extend(["--visits", str(_coerce_int(visits, "visits"))])
    _append_timeout(args, arguments)


def _append_timeout(args: list[str], arguments: Mapping[str, Any]) -> None:
    timeout = arguments.get("timeout")
    if timeout is not None:
        args.extend(["--timeout", str(_coerce_float(timeout, "timeout"))])


def _run_kgteach(args: Sequence[str]) -> JsonObject:
    command = [sys.executable, "-m", "kgteach.cli", *args]
    result = subprocess.run(
        command,
        cwd=Path.cwd(),
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "schema_version": "0.1.0",
            "command": "mcp.run_kgteach",
            "error": {
                "code": "KATAGO_PROTOCOL_ERROR",
                "message": f"kgteach returned non-JSON stdout: {exc.msg}",
            },
            "warnings": [],
            "partial": {
                "returncode": result.returncode,
                "stderr": result.stderr[-2000:],
            },
        }
    if not isinstance(payload, dict):
        raise ValueError("kgteach CLI returned a non-object JSON payload.")
    return payload


def _required_str(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required string argument: {name}")
    return value


def _required_int(arguments: Mapping[str, Any], name: str) -> int:
    return _coerce_int(arguments.get(name), name)


def _coerce_int(value: object, name: str) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Argument {name} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise ValueError(f"Argument {name} must be an integer.")
    if not isinstance(value, str):
        raise ValueError(f"Argument {name} must be an integer.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Argument {name} must be an integer.") from exc


def _coerce_float(value: object, name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Argument {name} must be a number.")
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Argument {name} must be a number.")
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"Argument {name} must be a number.") from exc


def _string_list_argument(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return ",".join(items)
    raise ValueError(f"Argument {name} must be a non-empty string or list.")


def _space_list_argument(arguments: Mapping[str, Any], name: str) -> str:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        items = [str(item).strip() for item in value if str(item).strip()]
        if items:
            return " ".join(items)
    raise ValueError(f"Argument {name} must be a non-empty string or list.")


def _tool_definitions() -> list[JsonObject]:
    return [
        _tool(
            "engine_health",
            "Check kgteach, KataGo, model, config, daemon, and human policy availability.",
            {},
        ),
        _tool("daemon_status", "Return local kgteach daemon status.", {}),
        _tool(
            "game_inspect",
            "Inspect SGF metadata without engine analysis.",
            {"sgf_path": _string_schema("Path to an SGF file.")},
            required=["sgf_path"],
        ),
        _tool(
            "teach_move",
            "Analyze and explain one move from an SGF.",
            _teaching_properties(),
            required=["sgf_path", "turn"],
        ),
        _tool(
            "teach_compare",
            "Compare candidate moves at a turn.",
            {
                "sgf_path": _string_schema("Path to an SGF file."),
                "turn": _integer_schema("One-based move turn to review."),
                "moves": _string_or_array_schema(
                    "Candidate moves such as D4,Q16 or a string array."
                ),
                "visits": _integer_schema("KataGo max visits."),
                "timeout": _number_schema("Timeout in seconds."),
            },
            required=["sgf_path", "turn", "moves"],
        ),
        _tool(
            "teach_line",
            "Validate a proposed variation line at a turn.",
            {
                "sgf_path": _string_schema("Path to an SGF file."),
                "turn": _integer_schema("One-based move turn for the variation base."),
                "line": _string_or_array_schema(
                    "Variation moves such as D4 Q16 or a string array."
                ),
                "timeout": _number_schema("Timeout in seconds."),
            },
            required=["sgf_path", "turn", "line"],
        ),
        _tool(
            "teach_rank_compare",
            "Compare human-policy likelihoods across ranks using KataGo Human SL.",
            {
                **_teaching_properties(),
                "ranks": _string_or_array_schema("Ranks such as 15k,8k,1k or [\"15k\",\"8k\"]."),
            },
            required=["sgf_path", "turn", "ranks"],
        ),
    ]


def _tool(
    name: str,
    description: str,
    properties: Mapping[str, Any],
    *,
    required: Sequence[str] = (),
) -> JsonObject:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": dict(properties),
            "required": list(required),
            "additionalProperties": False,
        },
    }


def _teaching_properties() -> dict[str, Any]:
    return {
        "sgf_path": _string_schema("Path to an SGF file."),
        "turn": _integer_schema("One-based move turn to review."),
        "student_rank": _string_schema("Optional student rank such as 8k."),
        "visits": _integer_schema("KataGo max visits."),
        "timeout": _number_schema("Timeout in seconds."),
    }


def _string_schema(description: str) -> JsonObject:
    return {"type": "string", "description": description}


def _integer_schema(description: str) -> JsonObject:
    return {"type": "integer", "description": description, "minimum": 0}


def _number_schema(description: str) -> JsonObject:
    return {"type": "number", "description": description, "exclusiveMinimum": 0}


def _string_or_array_schema(description: str) -> JsonObject:
    return {
        "description": description,
        "oneOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}, "minItems": 1},
        ],
    }


def _error_response(request_id: object, code: int, message: str) -> JsonObject:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


if __name__ == "__main__":
    raise SystemExit(main())
