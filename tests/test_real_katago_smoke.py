"""Optional smoke tests against a real local KataGo install."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kgteach.config import resolve_katago_config
from kgteach.katago import build_analysis_query
from kgteach.runtime import analyze_query_sync, check_runtime_ready, query_version_sync

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(
    os.environ.get("KGTEACH_REAL_KATAGO") != "1",
    reason="set KGTEACH_REAL_KATAGO=1 to run real KataGo smoke tests",
)
def test_real_katago_query_version_and_one_turn_analysis() -> None:
    """Run a tiny analysis request against a real local KataGo installation."""

    config = resolve_katago_config()
    readiness = check_runtime_ready(config)
    assert readiness["ready"] is True, readiness

    version = query_version_sync(config=config, timeout=30.0)
    assert version.get("id") == "query_version"

    query = build_analysis_query(
        request_id="real-smoke",
        moves=[],
        initial_stones=[],
        rules="japanese",
        komi=6.5,
        board_size=(9, 9),
        analyze_turns=[0],
        max_visits=1,
        include_ownership=False,
        include_policy=True,
    )

    responses = analyze_query_sync(query, config=config, timeout=60.0)

    assert responses
    assert responses[0]["id"] == "real-smoke"
    assert responses[0]["turnNumber"] == 0
    assert "rootInfo" in responses[0]


@pytest.mark.skipif(
    os.environ.get("KGTEACH_REAL_KATAGO") != "1",
    reason="set KGTEACH_REAL_KATAGO=1 to run real KataGo smoke tests",
)
def test_real_katago_teach_move_cli_smoke() -> None:
    """Run one engine-backed teaching command and verify JSON-only stdout."""

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "kgteach.cli",
            "teach",
            "move",
            str(ROOT / "fixtures" / "simple_9x9.sgf"),
            "--turn",
            "1",
            "--visits",
            "1",
            "--timeout",
            "60",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert result.stdout.startswith("{")
    assert payload["ok"] is True
    assert payload["command"] == "teach.move"
    assert payload["data"]["analysis_required"] is False
    assert payload["warnings"] == []
