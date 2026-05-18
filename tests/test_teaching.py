"""Tests for teaching heuristics and knowledge hooks."""

from __future__ import annotations

from kgteach.teaching import (
    classify_severity,
    compare_candidate_moves,
    generate_knowledge_hooks,
    summarize_move_explanation,
    teaching_priority,
)


def test_classify_severity_uses_score_loss_thresholds() -> None:
    assert classify_severity(0.0) == "excellent"
    assert classify_severity(0.49) == "excellent"
    assert classify_severity(0.5) == "good"
    assert classify_severity(1.49) == "good"
    assert classify_severity(1.5) == "inaccuracy"
    assert classify_severity(2.99) == "inaccuracy"
    assert classify_severity(3.0) == "mistake"
    assert classify_severity(6.99) == "mistake"
    assert classify_severity(7.0) == "blunder"


def test_teaching_priority_is_deterministic_bounded_and_combines_signals() -> None:
    quiet_shape = teaching_priority(
        score_loss=0.4,
        winrate_loss=0.01,
        level_appropriateness=0.25,
        concept_clarity=0.25,
        recurrence_count=0,
    )
    urgent_pattern = teaching_priority(
        score_loss=6.0,
        winrate_loss=0.18,
        level_appropriateness=0.8,
        concept_clarity=0.9,
        recurrence_count=3,
    )

    assert 0.0 <= quiet_shape <= 1.0
    assert 0.0 <= urgent_pattern <= 1.0
    assert urgent_pattern > quiet_shape
    assert urgent_pattern == teaching_priority(
        score_loss=6.0,
        winrate_loss=0.18,
        level_appropriateness=0.8,
        concept_clarity=0.9,
        recurrence_count=3,
    )
    assert teaching_priority(
        score_loss=100.0,
        winrate_loss=100.0,
        level_appropriateness=2.0,
        concept_clarity=2.0,
        recurrence_count=100,
    ) == 1.0
    assert teaching_priority(
        score_loss=-5.0,
        winrate_loss=-0.5,
        level_appropriateness=-1.0,
        concept_clarity=-1.0,
        recurrence_count=-10,
    ) == 0.0


def test_generate_knowledge_hooks_adds_joseki_and_principle_queries() -> None:
    hooks = generate_knowledge_hooks(
        concept_tags=["Corner Joseki", "shape", "shape", "endgame"],
        position_fingerprint="sgf:abcd:42",
        knowledge_refs=[
            {"id": "shape-001", "title": "Cutting points and shape basics"},
        ],
    )

    assert hooks["concept_tags"] == ["corner joseki", "shape", "endgame"]
    assert hooks["position_fingerprint"] == "sgf:abcd:42"
    assert hooks["knowledge_refs"] == [
        {"id": "shape-001", "title": "Cutting points and shape basics"},
    ]
    assert hooks["knowledge_queries"] == [
        {
            "kind": "joseki",
            "tag": "corner joseki",
            "query": "corner joseki reference for sgf:abcd:42",
        },
        {
            "kind": "principle",
            "tag": "shape",
            "query": "shape principle for sgf:abcd:42",
        },
        {
            "kind": "principle",
            "tag": "endgame",
            "query": "endgame principle for sgf:abcd:42",
        },
    ]
    assert hooks["suggested_followups"] == [
        "Review joseki alternatives for corner joseki at sgf:abcd:42.",
        "Practice the shape principle in a nearby position.",
        "Practice the endgame principle in a nearby position.",
    ]


def test_compare_candidate_moves_represents_played_move_without_rank() -> None:
    analysis = {
        "played_move": "Q16",
        "candidates": [
            {
                "move": "D4",
                "rank": 0,
                "score_loss": 0.0,
                "winrate_loss": 0.0,
                "concept_tags": ["territory"],
            },
            {
                "move": "Q16",
                "score_loss": 4.2,
                "winrate_loss": 0.11,
                "concept_tags": ["corner joseki"],
            },
            {
                "move": "C17",
                "rank": 2,
                "score_loss": 2.5,
                "winrate_loss": 0.04,
                "concept_tags": ["shape"],
            },
        ],
    }

    comparison = compare_candidate_moves(analysis)

    assert comparison["best_move"] == "D4"
    assert comparison["played_move"] == "Q16"
    assert comparison["played"] == {
        "move": "Q16",
        "rank": None,
        "score_loss": 4.2,
        "winrate_loss": 0.11,
        "severity": "mistake",
        "concept_tags": ["corner joseki"],
        "is_played": True,
    }
    assert comparison["candidates"][1]["move"] == "Q16"
    assert comparison["candidates"][1]["rank"] is None
    assert comparison["candidates"][1]["is_played"] is True


def test_summarize_move_explanation_adds_priority_and_hooks() -> None:
    analysis = {
        "turn": 42,
        "played_move": "Q16",
        "position_fingerprint": "sgf:abcd:42",
        "candidates": [
            {
                "move": "D4",
                "rank": 0,
                "score_loss": 0.0,
                "winrate_loss": 0.0,
                "concept_tags": ["territory"],
            },
            {
                "move": "Q16",
                "score_loss": 7.5,
                "winrate_loss": 0.2,
                "level_appropriateness": 0.85,
                "concept_clarity": 0.9,
                "recurrence_count": 2,
                "concept_tags": ["joseki", "shape"],
            },
        ],
        "knowledge_refs": [{"id": "joseki-004", "title": "Low approach joseki"}],
    }

    summary = summarize_move_explanation(analysis)

    assert summary["turn"] == 42
    assert summary["headline"] == "Q16 is a blunder worth reviewing."
    assert summary["played"]["move"] == "Q16"
    assert summary["played"]["rank"] is None
    assert summary["severity"] == "blunder"
    assert 0.0 <= summary["teaching_priority"] <= 1.0
    assert summary["knowledge_hooks"]["concept_tags"] == ["joseki", "shape"]
    assert summary["knowledge_hooks"]["knowledge_queries"][0]["kind"] == "joseki"
