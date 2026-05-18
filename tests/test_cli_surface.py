"""CLI surface tests for agent-facing command groups."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from kgteach.config import KataGoConfig

ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> dict[str, object]:
    result = subprocess.run(
        [sys.executable, "-m", "kgteach.cli", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.stdout
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "0.1.0"
    assert not result.stdout.startswith("usage:")
    return payload


def test_policy_value_maps_gtp_coordinates_to_katago_policy_order() -> None:
    from kgteach.cli import _policy_index, _policy_value

    policy = [0.0] * 82
    policy[0] = 0.11
    policy[48] = 0.22
    policy[81] = 0.33

    assert _policy_index("A9", 9) == 0
    assert _policy_index("D4", 9) == 48
    assert _policy_index("pass", 9) == 81
    assert _policy_value(policy, "A9", 9) == 0.11
    assert _policy_value(policy, "D4", 9) == 0.22
    assert _policy_value(policy, "pass", 9) == 0.33
    assert _policy_value(policy, "I9", 9) is None


def test_rank_compare_uses_human_policy_not_engine_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kgteach import cli
    from kgteach.game import normalize_sgf

    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee])")
    human_policy = [0.0] * 82
    human_policy[40] = 0.31
    human_policy[48] = 0.04
    engine_policy = [0.99] * 82

    calls: list[str | None] = []

    def fake_analysis_for_game(
        _game: dict[str, Any],
        *,
        turns: list[int],
        visits: int,
        timeout: float,
        include_ownership: bool = False,
        human_sl_profile: str | None = None,
    ) -> tuple[dict[int, dict[str, Any]], list[dict[str, str]]]:
        calls.append(human_sl_profile)
        assert turns == [0]
        assert visits == 1
        assert timeout == 1.0
        assert include_ownership is False
        if human_sl_profile is None:
            return {0: {"best_moves": [{"move": "D4"}]}}, []
        return {0: {"human_policy": human_policy, "policy": engine_policy}}, []

    monkeypatch.setattr(
        cli,
        "resolve_katago_config",
        lambda: KataGoConfig(human_model="human.bin.gz"),
    )
    monkeypatch.setattr(cli, "_analysis_for_game", fake_analysis_for_game)

    analysis, warnings = cli._rank_compare_analysis(
        game,
        1,
        ranks=["8k"],
        visits=1,
        timeout=1.0,
    )

    assert warnings == []
    assert calls == [None, "rank_8k"]
    assert analysis is not None
    assert analysis["human_policy"]["8k"]["played_policy"] == 0.31
    assert analysis["human_policy"]["8k"]["best_policy"] == 0.04


def test_rank_compare_does_not_fallback_to_engine_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kgteach import cli
    from kgteach.game import normalize_sgf

    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee])")

    def fake_analysis_for_game(
        _game: dict[str, Any],
        *,
        turns: list[int],
        visits: int,
        timeout: float,
        include_ownership: bool = False,
        human_sl_profile: str | None = None,
    ) -> tuple[dict[int, dict[str, Any]], list[dict[str, str]]]:
        if human_sl_profile is None:
            return {0: {"best_moves": [{"move": "D4"}]}}, []
        return {0: {"policy": [0.99] * 82}}, []

    monkeypatch.setattr(
        cli,
        "resolve_katago_config",
        lambda: KataGoConfig(human_model="human.bin.gz"),
    )
    monkeypatch.setattr(cli, "_analysis_for_game", fake_analysis_for_game)

    analysis, warnings = cli._rank_compare_analysis(
        game,
        1,
        ranks=["8k"],
        visits=1,
        timeout=1.0,
    )

    assert analysis is None
    assert warnings[0]["code"] == "HUMAN_POLICY_UNAVAILABLE"


def test_planned_commands_return_json_envelopes() -> None:
    commands = [
        ("daemon", "status"),
        ("config", "show"),
        ("game", "inspect", "missing.sgf"),
        ("game", "moves", "missing.sgf"),
        ("game", "position", "missing.sgf", "--turn", "1"),
        ("game", "slice", "missing.sgf", "--from-turn", "1", "--to-turn", "3"),
        ("analyze", "position", "--stdin"),
        ("analyze", "game", "missing.sgf"),
        ("teach", "plan", "missing.sgf"),
        ("teach", "summary", "missing.sgf"),
        ("teach", "move", "missing.sgf", "--turn", "1"),
        ("teach", "compare", "missing.sgf", "--turn", "1", "--moves", "D4,Q16"),
        ("teach", "line", "missing.sgf", "--turn", "1", "--line", "D4 Q16"),
        ("teach", "territory", "missing.sgf", "--turn", "1"),
        ("teach", "quiz", "missing.sgf", "--turn", "1"),
        ("teach", "mistakes", "missing.sgf"),
        ("teach", "rank-compare", "missing.sgf", "--turn", "1", "--ranks", "15k,8k"),
    ]

    for command in commands:
        payload = run_cli(*command)
        assert "ok" in payload
        assert "command" in payload
