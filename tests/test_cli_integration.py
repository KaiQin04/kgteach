"""CLI integration tests backed by local SGF fixtures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "simple_9x9.sgf"


def run_cli(*args: str) -> dict[str, object]:
    env = os.environ.copy()
    env["KGTEACH_DISABLE_GOREVIEW_DISCOVERY"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "kgteach.cli", *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.stdout
    return json.loads(result.stdout)


def test_game_commands_read_sgf_fixtures() -> None:
    inspect_payload = run_cli("game", "inspect", str(FIXTURE))
    assert inspect_payload["ok"] is True
    assert inspect_payload["data"]["move_count"] == 4

    moves_payload = run_cli("game", "moves", str(FIXTURE))
    assert moves_payload["data"]["moves"][2]["move"] == "pass"

    position_payload = run_cli("game", "position", str(FIXTURE), "--turn", "2")
    assert position_payload["data"]["next_player"] == "B"

    slice_payload = run_cli(
        "game",
        "slice",
        str(FIXTURE),
        "--from-turn",
        "2",
        "--to-turn",
        "3",
    )
    assert [move["turn"] for move in slice_payload["data"]["moves"]] == [2, 3]


def test_teach_move_returns_hooks_without_faking_engine_analysis() -> None:
    payload = run_cli("teach", "move", str(FIXTURE), "--turn", "1", "--student-rank", "8k")

    assert payload["ok"] is True
    assert payload["command"] == "teach.move"
    assert payload["data"]["played"]["move"] == "E5"
    assert payload["data"]["knowledge_hooks"]["knowledge_queries"]
    assert payload["warnings"][0]["code"] == "ENGINE_ANALYSIS_UNAVAILABLE"


def test_analyze_position_builds_query_from_stdin_json() -> None:
    position = {
        "board_size": 9,
        "rules": "chinese",
        "komi": 6.5,
        "moves": [["B", "E5"]],
        "initial_stones": [],
        "turns": [1],
        "max_visits": 16,
    }
    result = subprocess.run(
        [sys.executable, "-m", "kgteach.cli", "analyze", "position", "--stdin"],
        cwd=ROOT,
        env={**os.environ, "KGTEACH_DISABLE_GOREVIEW_DISCOVERY": "1"},
        input=json.dumps(position),
        check=False,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["ok"] is True
    assert payload["data"]["query"]["boardXSize"] == 9
    assert payload["data"]["query"]["analyzeTurns"] == [1]
    assert payload["warnings"][0]["code"] == "ENGINE_ANALYSIS_UNAVAILABLE"


def test_parse_errors_still_return_json_envelope() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "kgteach.cli", "teach", "move", str(FIXTURE)],
        cwd=ROOT,
        env={**os.environ, "KGTEACH_DISABLE_GOREVIEW_DISCOVERY": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)

    assert result.returncode == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert result.stdout.startswith("{")


def test_teach_compare_missing_analysis_does_not_emit_infinity() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kgteach.cli",
            "teach",
            "compare",
            str(FIXTURE),
            "--turn",
            "1",
            "--moves",
            "E5,D4",
        ],
        cwd=ROOT,
        env={**os.environ, "KGTEACH_DISABLE_GOREVIEW_DISCOVERY": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout, parse_constant=lambda value: (_ for _ in ()).throw(
        AssertionError(value)
    ))

    assert "Infinity" not in result.stdout
    assert payload["ok"] is True
    assert payload["data"]["best_move"] is None
    assert payload["data"]["candidates"][0]["score_loss"] is None
