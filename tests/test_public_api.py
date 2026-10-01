"""The package root exposes the library surface the GoReview worker depends on."""

from __future__ import annotations

import importlib.metadata

import kgteach


def test_public_names_are_exported() -> None:
    """Expose every documented library entry point at the package root."""

    expected = {
        "SCHEMA_VERSION", "ErrorCode", "envelope_ok", "envelope_error",
        "KataGoConfig", "resolve_katago_config", "AnalysisRuntime",
        "build_query_from_game", "normalize_analysis_responses", "normalize_sgf",
        "teach_move", "teach_compare", "teach_plan", "teach_summary", "teach_line",
        "teach_territory", "teach_quiz", "teach_mistakes", "teach_rank_compare",
        "classify_severity",
    }
    assert expected <= set(kgteach.__all__)
    for name in expected:
        assert getattr(kgteach, name) is not None


def test_game_dict_pipeline_without_engine() -> None:
    """A linearized history (no SGF file) flows through query, normalize and teach."""

    game = {
        "board_size": 9,
        "rules": "chinese",
        "komi": 7.5,
        "initial_stones": [],
        "moves": [
            {"turn": 1, "player": "B", "move": "E5"},
            {"turn": 2, "player": "W", "move": "C3"},
        ],
    }
    query = kgteach.build_query_from_game(game, request_id="r", turns=[2], visits=8)
    assert query["analyzeTurns"] == [2] and query["rules"] == "chinese"

    response = {
        "id": "r",
        "turnNumber": 2,
        "rootInfo": {"currentPlayer": "B", "visits": 8, "winrate": 0.6, "scoreLead": 2.0},
        "moveInfos": [
            {"move": "G7", "visits": 5, "winrate": 0.62, "scoreLead": 2.4, "pv": ["G7"]},
            {"move": "C3", "visits": 3, "winrate": 0.5, "scoreLead": 0.0, "pv": ["C3"]},
        ],
    }
    normalized = kgteach.normalize_analysis_responses([response], perspective="black")
    position = normalized["positions"][0]
    teaching = kgteach.teach_move(game, turn=2, analysis=position)
    assert teaching["analysis_required"] is False
    assert teaching["best_move"] == "G7"
    assert kgteach.classify_severity(2.4) == "inaccuracy"


def test_version_is_0_2_0() -> None:
    """Install matching package metadata for the public API release."""

    assert importlib.metadata.version("kgteach") == "0.2.0"
