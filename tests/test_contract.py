"""Tests for the stable agent-facing JSON contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from kgteach.contract import ErrorCode, envelope_error, envelope_ok

ROOT = Path(__file__).resolve().parents[1]


def test_success_envelope_has_stable_shape() -> None:
    payload = envelope_ok(command="engine.health", data={"katago_found": False})

    assert payload == {
        "ok": True,
        "schema_version": "0.1.0",
        "command": "engine.health",
        "data": {"katago_found": False},
        "warnings": [],
        "debug": None,
    }


def test_error_envelope_is_machine_readable() -> None:
    payload = envelope_error(
        command="teach.move",
        code=ErrorCode.TIMEOUT,
        message="Analysis did not finish within 30 seconds.",
        partial={"available": True},
    )

    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "TIMEOUT",
        "message": "Analysis did not finish within 30 seconds.",
    }
    assert payload["partial"] == {"available": True}


def test_engine_health_stdout_is_json_only() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "kgteach.cli", "engine", "health"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    decoded = json.loads(result.stdout)
    assert decoded["ok"] is True
    assert decoded["command"] == "engine.health"
    assert "Hello from kgteach!" not in result.stdout


def test_engine_schema_lists_agent_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "kgteach.cli", "engine", "schema"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    decoded = json.loads(result.stdout)
    commands = decoded["data"]["commands"]
    assert "teach.move" in commands
    assert "teach.rank_compare" in commands
