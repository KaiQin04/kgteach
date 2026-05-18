"""Tests for the kgteach stdio MCP wrapper."""

from __future__ import annotations

import json
from typing import Any

from kgteach import mcp_server


def request(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send one in-memory JSON-RPC request to the MCP handler."""

    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    response = mcp_server.handle_json_line(json.dumps(payload))
    assert response is not None
    return response


def test_initialize_and_tools_list() -> None:
    initialize = request("initialize", {"protocolVersion": "test-version"})

    assert initialize["result"]["protocolVersion"] == "test-version"
    assert initialize["result"]["serverInfo"]["name"] == "kgteach"

    tools = request("tools/list")
    names = {tool["name"] for tool in tools["result"]["tools"]}

    assert "engine_health" in names
    assert "teach_move" in names
    assert "teach_rank_compare" in names


def test_notification_returns_no_response() -> None:
    raw = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})

    assert mcp_server.handle_json_line(raw) is None


def test_tool_call_wraps_kgteach_json(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        mcp_server,
        "_run_kgteach",
        lambda args: {
            "ok": True,
            "schema_version": "0.1.0",
            "command": ".".join(args[:2]),
            "data": {"args": args},
            "warnings": [],
            "debug": None,
        },
    )

    response = request(
        "tools/call",
        {
            "name": "teach_move",
            "arguments": {
                "sgf_path": "fixtures/simple_9x9.sgf",
                "turn": 1,
                "student_rank": "8k",
                "visits": 1,
            },
        },
    )

    result = response["result"]
    payload = json.loads(result["content"][0]["text"])

    assert result["isError"] is False
    assert payload["data"]["args"] == [
        "teach",
        "move",
        "fixtures/simple_9x9.sgf",
        "--turn",
        "1",
        "--student-rank",
        "8k",
        "--visits",
        "1",
    ]


def test_tool_call_reports_input_errors() -> None:
    response = request(
        "tools/call",
        {"name": "teach_move", "arguments": {"sgf_path": "game.sgf"}},
    )

    assert response["error"]["code"] == -32602
