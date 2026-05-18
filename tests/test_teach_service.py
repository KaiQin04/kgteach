"""Tests for pure teaching command service helpers."""

from __future__ import annotations

import pytest

from kgteach.game import normalize_sgf
from kgteach.knowledge import list_knowledge_packs, query_knowledge_hooks
from kgteach.teach_service import (
    teach_compare,
    teach_line,
    teach_mistakes,
    teach_move,
    teach_plan,
    teach_quiz,
    teach_rank_compare,
    teach_summary,
    teach_territory,
)


def test_missing_analysis_returns_structured_markers() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee];W[dd])")

    move = teach_move(game, turn=1)
    plan = teach_plan(game)
    summary = teach_summary(game)
    territory = teach_territory(game, turn=1)
    mistakes = teach_mistakes(game)

    assert move["analysis_required"] is True
    assert move["played"]["move"] == "E5"
    assert move["warnings"][0]["code"] == "ANALYSIS_REQUIRED"
    assert plan["analysis_required"] is True
    assert plan["review_plan"]["moments"][0]["played"] == "E5"
    assert summary["analysis_required"] is True
    assert summary["summary"]["game_length"] == 2
    assert territory["analysis_required"] is True
    assert territory["regions"] == []
    assert mistakes["analysis_required"] is True
    assert mistakes["mistakes"] == []


def test_teach_move_represents_played_move_missing_from_top_candidates() -> None:
    game = normalize_sgf(
        "(;SZ[19]KM[6.5]RU[Japanese];B[pd];W[dp];B[pp])"
    )
    analysis = {
        "turn": 3,
        "played_move": "Q4",
        "best_moves": [
            {"move": "D16", "rank": 0, "score_loss": 0.0, "winrate_loss": 0.0},
            {"move": "C3", "rank": 1, "score_loss": 1.2, "winrate_loss": 0.03},
        ],
        "score_loss": 4.5,
        "winrate_loss": 0.12,
        "concept_tags": ["tenuki", "corner direction"],
    }

    payload = teach_move(game, turn=3, analysis=analysis, top_n=2)

    assert payload["analysis_required"] is False
    assert payload["best_move"] == "D16"
    assert payload["played"] == {
        "move": "Q4",
        "rank": None,
        "score_loss": 4.5,
        "winrate_loss": 0.12,
        "severity": "mistake",
        "concept_tags": ["tenuki", "corner direction"],
        "is_played": True,
        "analysis_required": True,
    }
    assert payload["comparison"]["candidates"][-1]["move"] == "Q4"


def test_teach_plan_sorts_by_teaching_priority_not_score_loss_only() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee];W[dd])")
    analyses = [
        {
            "turn": 1,
            "played_move": "E5",
            "score_loss": 8.0,
            "winrate_loss": 0.04,
            "level_appropriateness": 0.1,
            "concept_clarity": 0.1,
            "recurrence_count": 0,
            "concept_tags": ["large loss"],
        },
        {
            "turn": 2,
            "played_move": "D6",
            "score_loss": 3.2,
            "winrate_loss": 0.18,
            "level_appropriateness": 0.95,
            "concept_clarity": 0.95,
            "recurrence_count": 4,
            "concept_tags": ["cutting point", "shape"],
        },
    ]

    payload = teach_plan(game, analysis=analyses, max_moments=2)

    moments = payload["review_plan"]["moments"]
    assert [moment["turn"] for moment in moments] == [2, 1]
    assert moments[0]["teaching_priority"] > moments[1]["teaching_priority"]
    assert moments[0]["score_loss"] < moments[1]["score_loss"]


def test_teach_line_validates_coordinates_occupancy_and_pass() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee];W[dd])")

    payload = teach_line(game, turn=2, line=["pass", "C7"])

    assert payload["steps"][0]["move"] == "pass"
    assert payload["steps"][0]["is_pass"] is True
    assert payload["steps"][1]["move"] == "C7"

    with pytest.raises(ValueError, match="Invalid coordinate"):
        teach_line(game, turn=2, line=["I9"])
    with pytest.raises(ValueError, match="occupied"):
        teach_line(game, turn=2, line=["E5"])


def test_teach_line_handles_capture_suicide_and_simple_ko() -> None:
    capture_game = {
        "board_size": 5,
        "rules": "japanese",
        "initial_stones": [
            {"player": "W", "move": "C3"},
            {"player": "B", "move": "B3"},
            {"player": "B", "move": "D3"},
            {"player": "B", "move": "C2"},
        ],
        "moves": [],
    }

    capture = teach_line(capture_game, turn=0, line=["C4"])

    assert capture["steps"][0]["player"] == "B"
    assert capture["steps"][0]["captured"] == ["C3"]

    suicide_game = {
        "board_size": 5,
        "rules": "japanese",
        "initial_stones": [
            {"player": "W", "move": "B3"},
            {"player": "W", "move": "D3"},
            {"player": "W", "move": "C2"},
            {"player": "W", "move": "C4"},
        ],
        "moves": [],
    }

    with pytest.raises(ValueError, match="suicide"):
        teach_line(suicide_game, turn=0, line=["C3"])

    ko_game = {
        "board_size": 5,
        "rules": "japanese",
        "initial_stones": [
            {"player": "W", "move": "C3"},
            {"player": "W", "move": "B4"},
            {"player": "W", "move": "D4"},
            {"player": "W", "move": "C5"},
            {"player": "B", "move": "B3"},
            {"player": "B", "move": "C2"},
            {"player": "B", "move": "D3"},
        ],
        "moves": [],
    }

    with pytest.raises(ValueError, match="simple ko"):
        teach_line(ko_game, turn=0, line=["C4", "C3"])


def test_territory_compacts_ownership_array_into_broad_regions() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee];W[dd])")
    ownership = [0.0] * 81
    for row in range(3):
        for col in range(3):
            ownership[row * 9 + col] = 0.8
    for row in range(6, 9):
        for col in range(6, 9):
            ownership[row * 9 + col] = -0.7

    payload = teach_territory(game, turn=2, analysis={"ownership": ownership})

    regions = {region["region"]: region for region in payload["regions"]}
    assert payload["analysis_required"] is False
    assert regions["upper_left"]["owner"] == "B"
    assert regions["upper_left"]["point_count"] == 9
    assert regions["lower_right"]["owner"] == "W"
    assert regions["lower_right"]["point_count"] == 9


def test_compare_quiz_and_mistakes_use_provided_analysis_only() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee];W[dd];B[cc])")
    analyses = [
        {
            "turn": 1,
            "played_move": "E5",
            "best_moves": [
                {"move": "C7", "rank": 0, "score_loss": 0.0, "winrate_loss": 0.0},
                {"move": "E5", "rank": 2, "score_loss": 2.0, "winrate_loss": 0.08},
            ],
            "concept_tags": ["opening"],
        },
        {
            "turn": 3,
            "played_move": "C7",
            "best_moves": [
                {
                    "move": "D6",
                    "rank": 0,
                    "score_loss": 0.0,
                    "winrate_loss": 0.0,
                    "concept_tags": ["shape"],
                },
                {
                    "move": "C7",
                    "rank": 4,
                    "score_loss": 7.2,
                    "winrate_loss": 0.19,
                    "concept_tags": ["atari"],
                },
            ],
        },
    ]

    comparison = teach_compare(
        game,
        turn=1,
        moves=["E5", "C7"],
        analysis=analyses,
    )
    quiz = teach_quiz(game, turn=3, analysis=analyses)
    mistakes = teach_mistakes(game, analysis=analyses, min_severity="mistake")

    assert comparison["best_move"] == "C7"
    assert [candidate["move"] for candidate in comparison["candidates"]] == ["C7", "E5"]
    assert quiz["quiz"]["answer"] == "D6"
    assert quiz["quiz"]["choices"] == ["D6", "C7"]
    assert mistakes["mistakes"][0]["turn"] == 3
    assert mistakes["mistakes"][0]["severity"] == "blunder"


def test_rank_compare_reports_no_human_policy_without_human_data() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee])")

    payload = teach_rank_compare(game, turn=1, ranks=["15k", "8k"], analysis={})

    assert payload["human_policy_available"] is False
    assert payload["ranks"] == ["15k", "8k"]
    assert payload["rank_deltas"] == []
    assert payload["warnings"][0]["code"] == "HUMAN_POLICY_REQUIRED"


def test_rank_compare_reports_human_likelihood_for_played_and_best() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee])")
    analysis = {
        "turn": 1,
        "played_move": "E5",
        "best_move": "D4",
        "human_policy": {
            "15k": {
                "profile": "rank_15k",
                "policy_available": True,
                "policy_size": 82,
                "played_move": "E5",
                "played_policy": 0.31,
                "best_move": "D4",
                "best_policy": 0.04,
            },
            "8k": {
                "profile": "rank_8k",
                "policy_available": True,
                "policy_size": 82,
                "played_move": "E5",
                "played_policy": 0.18,
                "best_move": "D4",
                "best_policy": 0.14,
            },
        },
    }

    payload = teach_rank_compare(
        game,
        turn=1,
        ranks=["15k", "8k"],
        analysis=analysis,
    )

    assert payload["human_policy_available"] is True
    assert payload["played_move"] == "E5"
    assert payload["best_move"] == "D4"
    assert payload["human_likelihood"]["played_move"] == {"15k": 0.31, "8k": 0.18}
    assert payload["human_likelihood"]["best_move"] == {"15k": 0.04, "8k": 0.14}
    assert payload["rank_deltas"][0]["played_minus_best_policy"] == 0.27


def test_knowledge_registry_exposes_pack_metadata_and_query_hooks() -> None:
    packs = list_knowledge_packs()

    assert set(packs) == {
        "joseki",
        "principles",
        "tesuji",
        "life_death",
        "endgame",
        "pedagogy",
    }
    assert packs["joseki"]["hook"] == "knowledge.joseki.query"
    assert packs["pedagogy"]["rank_aware"] is True

    hooks = query_knowledge_hooks(
        concept_tags=["Corner Joseki", "tesuji", "shape"],
        position_fingerprint="sgf:9:2",
        student_rank="8k",
    )

    assert [hook["pack"] for hook in hooks["knowledge_queries"]] == [
        "joseki",
        "tesuji",
        "principles",
    ]
    assert hooks["knowledge_queries"][0]["hook"] == "knowledge.joseki.query"
    assert hooks["knowledge_queries"][0]["student_rank"] == "8k"
