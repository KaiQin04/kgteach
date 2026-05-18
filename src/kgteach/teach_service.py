"""Pure teaching command services built from normalized game and analysis data."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

from kgteach.go_rules import validate_variation
from kgteach.knowledge import query_knowledge_hooks
from kgteach.teaching import (
    classify_severity,
    summarize_move_explanation,
    teaching_priority,
)

Analysis = Mapping[str, Any]

_SEVERITY_ORDER = {
    "excellent": 0,
    "good": 1,
    "inaccuracy": 2,
    "mistake": 3,
    "blunder": 4,
}


def teach_move(
    game: dict[str, Any],
    *,
    turn: int,
    analysis: Analysis | None = None,
    student_rank: str | None = None,
    top_n: int = 6,
) -> dict[str, Any]:
    """Build one move teaching payload.

    Args:
        game: Normalized game payload.
        turn: One-based move number.
        analysis: Optional analysis for the requested turn.
        student_rank: Optional rank label used only for knowledge hooks.
        top_n: Maximum number of engine candidates before adding the played move.

    Returns:
        A compact teaching payload with played move, comparison, and hooks.
    """

    move = _move_at(game, turn)
    analysis_required = analysis is None
    normalized = _analysis_for_turn(analysis, turn)
    candidates = _candidate_list(normalized, top_n=top_n)
    played_move = str(normalized.get("played_move") or move["move"])

    if not candidates:
        candidates = [
            {
                "move": played_move,
                "rank": None,
                "score_loss": 0.0,
                "winrate_loss": 0.0,
                "severity": "excellent",
                "concept_tags": ["analysis required"],
                "is_played": True,
            }
        ]
    elif not _contains_move(candidates, played_move):
        candidates.append(_synthetic_played_candidate(normalized, played_move))

    payload = summarize_move_explanation(
        {
            "turn": turn,
            "played_move": played_move,
            "position_fingerprint": _position_fingerprint(game, turn),
            "candidates": candidates,
            "knowledge_refs": normalized.get("knowledge_refs", []),
        }
    )
    payload["best_move"] = payload["comparison"]["best_move"]
    payload["student_rank"] = student_rank
    payload["analysis_required"] = analysis_required
    payload["warnings"] = [] if analysis is not None else [_analysis_required_warning()]

    if not _contains_move(_candidate_list(normalized, top_n=top_n), played_move):
        payload["played"]["analysis_required"] = True
    return payload


def teach_compare(
    game: dict[str, Any],
    *,
    turn: int,
    moves: Sequence[str],
    analysis: Analysis | Sequence[Analysis] | None = None,
) -> dict[str, Any]:
    """Compare requested candidate moves from supplied analysis evidence."""

    normalized = _analysis_for_turn(analysis, turn)
    played = _move_at(game, turn)
    analyzed = {
        str(candidate["move"]): candidate
        for candidate in _candidate_list(normalized, top_n=max(len(moves), 1) + 8)
        if candidate.get("move") is not None
    }
    candidates: list[dict[str, Any]] = []
    for raw_move in moves:
        move = _normalize_text(raw_move)
        if move in analyzed:
            candidates.append(dict(analyzed[move]))
        else:
            candidates.append(
                {
                    "move": move,
                    "rank": None,
                    "score_loss": None,
                    "winrate_loss": None,
                    "severity": "analysis_required",
                    "concept_tags": ["candidate comparison"],
                    "is_played": move == played["move"],
                    "analysis_required": True,
                }
            )
    known_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("analysis_required") is not True
    ]
    unknown_candidates = [
        candidate
        for candidate in candidates
        if candidate.get("analysis_required") is True
    ]
    candidates = sorted(known_candidates, key=_candidate_sort_key) + unknown_candidates
    best_candidate = next(
        (
            candidate
            for candidate in candidates
            if candidate.get("analysis_required") is not True
        ),
        None,
    )
    best = None if best_candidate is None else best_candidate.get("move")
    hooks = query_knowledge_hooks(
        _tags_from_candidates(candidates),
        position_fingerprint=_position_fingerprint(game, turn),
    )
    return {
        "turn": turn,
        "played_move": played["move"],
        "best_move": best,
        "candidates": candidates,
        "knowledge_hooks": hooks,
        "analysis_required": analysis is None,
        "warnings": [] if analysis is not None else [_analysis_required_warning()],
    }


def teach_plan(
    game: dict[str, Any],
    *,
    max_moments: int = 6,
    student_rank: str | None = None,
    analysis: Sequence[Analysis] | Mapping[int, Analysis] | None = None,
    analysis_by_turn: Mapping[int, Analysis] | None = None,
) -> dict[str, Any]:
    """Create a review plan sorted by teaching value, not raw loss only."""

    lookup = _analysis_lookup(analysis, analysis_by_turn)
    moments: list[dict[str, Any]] = []
    for move in game["moves"]:
        turn = int(move["turn"])
        item = _analysis_for_turn(lookup, turn)
        candidates = _candidate_list(item)
        best_move = _best_move(candidates)
        score_loss = _played_score_loss(item, str(move["move"]))
        winrate_loss = _played_winrate_loss(item, str(move["move"]))
        tags = _concept_tags(item, candidates)
        priority = teaching_priority(
            score_loss=score_loss,
            winrate_loss=winrate_loss,
            level_appropriateness=_float(item.get("level_appropriateness"), 0.7),
            concept_clarity=_float(item.get("concept_clarity"), 0.6),
            recurrence_count=int(_float(item.get("recurrence_count"), 0.0)),
        )
        moments.append(
            {
                "turn": turn,
                "player": move["player"],
                "played": item.get("played_move", move["move"]),
                "best": best_move,
                "severity": classify_severity(score_loss),
                "score_loss": score_loss,
                "winrate_loss": winrate_loss,
                "teaching_priority": priority,
                "concept_tags": tags or ["analysis required"],
                "knowledge_hooks": query_knowledge_hooks(
                    tags or ["analysis required"],
                    position_fingerprint=_position_fingerprint(game, turn),
                    student_rank=student_rank,
                ),
                "suggested_followups": [
                    {
                        "command": "kgteach teach move",
                        "args": ["<sgf>", "--turn", str(turn)],
                    }
                ],
            }
        )
    moments.sort(key=lambda item: item["teaching_priority"], reverse=True)
    return {
        "analysis_required": not bool(lookup),
        "review_plan": {
            "student_rank": student_rank,
            "recommended_focus": _recommended_focus(moments),
            "moments": moments[:max_moments],
        },
        "warnings": [] if lookup else [_analysis_required_warning()],
    }


def teach_summary(
    game: dict[str, Any],
    *,
    analysis: Sequence[Analysis] | Mapping[int, Analysis] | None = None,
    student_rank: str | None = None,
) -> dict[str, Any]:
    """Return a compact game storyline scaffold."""

    lookup = _analysis_lookup(analysis, None)
    moments = teach_plan(
        game,
        max_moments=3,
        student_rank=student_rank,
        analysis=lookup,
    )["review_plan"]["moments"]
    lesson_tags = _recommended_focus(moments) or ["review candidate moves"]
    return {
        "analysis_required": not bool(lookup),
        "summary": {
            "game_length": len(game["moves"]),
            "main_story": [
                {
                    "phase": "whole_game",
                    "summary_hint": (
                        "Use the review plan as evidence; avoid long natural-language "
                        "commentary inside kgteach."
                    ),
                }
            ],
            "main_lessons": [f"Review {tag}." for tag in lesson_tags],
        },
        "warnings": [] if lookup else [_analysis_required_warning()],
    }


def teach_line(game: dict[str, Any], *, turn: int, line: Sequence[str]) -> dict[str, Any]:
    """Validate and summarize a variation line with local legality checks."""

    legal_moves = validate_variation(game, turn=turn, line=line)
    steps = []
    for ply, legal_move in enumerate(legal_moves, start=1):
        steps.append(
            {
                "ply": ply,
                "player": legal_move.player,
                "move": legal_move.move,
                "is_pass": legal_move.is_pass,
                "captured": legal_move.captured,
                "ko_point": legal_move.ko_point,
                "comment_hint": (
                    "Legal by local rules check; "
                    "engine analysis is still recommended."
                ),
            }
        )
    return {
        "base_turn": turn,
        "line": [step["move"] for step in steps],
        "steps": steps,
        "analysis_required": True,
        "teaching": {
            "line_summary": "Variation passed basic coordinate and occupancy validation.",
            "critical_step": 1 if steps else None,
        },
        "warnings": [
            {
                "code": "ENGINE_VALIDATION_NOT_RUN",
                "message": (
                    "Local legality was checked; run KataGo analysis to evaluate "
                    "the variation."
                ),
            }
        ],
    }


def teach_territory(
    game: dict[str, Any],
    *,
    turn: int,
    analysis: Analysis | None = None,
) -> dict[str, Any]:
    """Compact ownership into coarse board regions when available."""

    ownership = None if analysis is None else analysis.get("ownership")
    if not isinstance(ownership, list):
        return {
            "turn": turn,
            "territory": None,
            "regions": [],
            "analysis_required": True,
            "warnings": [_analysis_required_warning()],
            "teaching": {"main_point": "Ownership requires KataGo includeOwnership analysis."},
        }

    board_size = int(game["board_size"])
    regions = _ownership_regions(ownership, board_size)
    average = sum(_float(value, 0.0) for value in ownership) / max(1, len(ownership))
    leader = _owner_from_average(average)
    return {
        "turn": turn,
        "territory": {
            "leader": leader,
            "ownership_average": round(average, 4),
        },
        "regions": regions,
        "analysis_required": False,
        "warnings": [],
        "teaching": {
            "main_point": "Ownership is compacted into coarse regions for agent context."
        },
    }


def teach_quiz(
    game: dict[str, Any],
    *,
    turn: int,
    analysis: Analysis | Sequence[Analysis] | None = None,
    quiz_type: str = "next-move",
    choices: int = 4,
) -> dict[str, Any]:
    """Generate a compact next-move style quiz from candidate evidence."""

    move = _move_at(game, turn)
    normalized = _analysis_for_turn(analysis, turn)
    candidates = _candidate_list(normalized, top_n=choices)
    if not candidates:
        candidates = [
            {
                "move": move["move"],
                "rank": None,
                "score_loss": 0.0,
                "winrate_loss": 0.0,
                "concept_tags": ["analysis required"],
                "is_played": True,
            }
        ]
    answer = None if analysis is None else _best_move(candidates)
    return {
        "analysis_required": analysis is None,
        "quiz": {
            "type": quiz_type.replace("-", "_"),
            "turn": turn,
            "player": move["player"],
            "question": {"prompt_hint": f"{move['player']} to play."},
            "choices": [str(candidate["move"]) for candidate in candidates[:choices]],
            "answer": answer,
            "explanation_hint": "Compare candidate score loss before writing the lesson.",
        },
        "warnings": [] if analysis is not None else [_analysis_required_warning()],
    }


def teach_mistakes(
    game: dict[str, Any],
    *,
    analysis: Sequence[Analysis] | Mapping[int, Analysis] | None = None,
    analysis_by_turn: Mapping[int, Analysis] | None = None,
    min_score_loss: float = 3.0,
    min_severity: str | None = None,
) -> dict[str, Any]:
    """Return mistakes that meet score-loss or severity thresholds."""

    lookup = _analysis_lookup(analysis, analysis_by_turn)
    threshold = _severity_threshold(min_severity)
    mistakes: list[dict[str, Any]] = []
    for move in game["moves"]:
        turn = int(move["turn"])
        item = _analysis_for_turn(lookup, turn)
        if not item:
            continue
        candidates = _candidate_list(item)
        score_loss = _played_score_loss(item, str(move["move"]))
        severity = classify_severity(score_loss)
        if score_loss < min_score_loss and _SEVERITY_ORDER[severity] < threshold:
            continue
        mistakes.append(
            {
                "turn": turn,
                "player": move["player"],
                "played": item.get("played_move", move["move"]),
                "best": _best_move(candidates),
                "score_loss": score_loss,
                "winrate_loss": _played_winrate_loss(item, str(move["move"])),
                "severity": severity,
                "concept_tags": _concept_tags(item, candidates),
            }
        )
    mistakes.sort(
        key=lambda item: (_SEVERITY_ORDER[item["severity"]], item["score_loss"]),
        reverse=True,
    )
    return {
        "analysis_required": not bool(lookup),
        "mistakes": mistakes,
        "warnings": [] if lookup else [_analysis_required_warning()],
    }


def teach_rank_compare(
    game: dict[str, Any],
    *,
    turn: int,
    ranks: Sequence[str],
    analysis: Analysis | None = None,
) -> dict[str, Any]:
    """Return rank-compare data when human policy is available."""

    move = _move_at(game, turn)
    analysis_data: Mapping[str, Any] = {} if analysis is None else analysis
    human_policy = analysis_data.get("human_policy")
    if not isinstance(human_policy, Mapping):
        return {
            "turn": turn,
            "ranks": list(ranks),
            "human_policy_available": False,
            "played_move": move["move"],
            "best_move": None,
            "human_likelihood": {},
            "rank_deltas": [],
            "warnings": [
                {
                    "code": "HUMAN_POLICY_REQUIRED",
                    "message": "Rank compare requires a configured KataGo human SL model.",
                }
            ],
        }
    played_move = str(analysis_data.get("played_move") or move["move"])
    best_move = analysis_data.get("best_move")
    best_move_text = None if best_move is None else str(best_move)
    rank_deltas = [_rank_policy_delta(rank, human_policy.get(rank, {})) for rank in ranks]
    return {
        "turn": turn,
        "ranks": list(ranks),
        "human_policy_available": True,
        "played_move": played_move,
        "best_move": best_move_text,
        "human_likelihood": {
            "played_move": {
                item["rank"]: item["played_policy"]
                for item in rank_deltas
                if item["played_policy"] is not None
            },
            "best_move": {
                item["rank"]: item["best_policy"]
                for item in rank_deltas
                if item["best_policy"] is not None
            },
        },
        "rank_deltas": rank_deltas,
        "warnings": [],
    }


def _rank_policy_delta(rank: str, policy: object) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        policy = {}
    played_policy = _optional_float(policy.get("played_policy"))
    best_policy = _optional_float(policy.get("best_policy"))
    return {
        "rank": rank,
        "profile": policy.get("profile"),
        "policy_available": bool(policy.get("policy_available")),
        "policy_size": _optional_int(policy.get("policy_size")) or 0,
        "played_move": policy.get("played_move"),
        "played_policy": played_policy,
        "best_move": policy.get("best_move"),
        "best_policy": best_policy,
        "played_minus_best_policy": _policy_delta(played_policy, best_policy),
    }


def _analysis_lookup(
    analysis: Sequence[Analysis] | Mapping[int, Analysis] | None,
    analysis_by_turn: Mapping[int, Analysis] | None,
) -> dict[int, Analysis]:
    if analysis_by_turn is not None:
        return {int(turn): item for turn, item in analysis_by_turn.items()}
    if analysis is None:
        return {}
    if isinstance(analysis, Mapping):
        raw_mapping: Mapping[Any, Any] = analysis
        result: dict[int, Analysis] = {}
        for key, value in raw_mapping.items():
            if isinstance(key, int) and isinstance(value, Mapping):
                result[int(key)] = cast(Analysis, value)
        if result:
            return result
        turn = _turn_from_analysis(raw_mapping)
        return {} if turn is None else {turn: cast(Analysis, raw_mapping)}
    return {
        turn: item
        for item in analysis
        if isinstance(item, Mapping) and (turn := _turn_from_analysis(item)) is not None
    }


def _analysis_for_turn(
    analysis: Analysis | Sequence[Analysis] | Mapping[int, Analysis] | None,
    turn: int,
) -> dict[str, Any]:
    if analysis is None:
        return {}
    if isinstance(analysis, Mapping):
        raw_mapping: Mapping[Any, Any] = analysis
        direct = raw_mapping.get(turn)
        if isinstance(direct, Mapping):
            return dict(direct)
        if any(isinstance(key, int) for key in raw_mapping):
            return {}
        if _turn_from_analysis(raw_mapping) in {None, turn}:
            return dict(raw_mapping)
        return {}
    for item in analysis:
        if isinstance(item, Mapping) and _turn_from_analysis(item) == turn:
            return dict(item)
    return {}


def _turn_from_analysis(analysis: Mapping[Any, Any]) -> int | None:
    for key in ("turn", "turn_number"):
        if key in analysis:
            return _optional_int(analysis.get(key))
    position = analysis.get("position")
    if isinstance(position, Mapping):
        for key in ("turn", "turn_number"):
            if key in position:
                return _optional_int(position.get(key))
    return None


def _candidate_list(analysis: Mapping[str, Any], *, top_n: int = 99) -> list[dict[str, Any]]:
    raw_candidates = analysis.get("best_moves") or analysis.get("candidates") or []
    if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, str):
        return []
    candidates = [
        _candidate_from_raw(item, analysis)
        for item in raw_candidates
        if isinstance(item, Mapping)
    ]
    candidates.sort(key=_candidate_sort_key)
    return candidates[:top_n]


def _candidate_from_raw(item: Mapping[str, Any], analysis: Mapping[str, Any]) -> dict[str, Any]:
    score_loss = _candidate_loss(item, analysis, "score_loss", "score_lead")
    winrate_loss = _candidate_loss(item, analysis, "winrate_loss", "winrate")
    move = str(item.get("move", "pass"))
    played_move = analysis.get("played_move")
    return {
        "move": move,
        "rank": _optional_int(item.get("rank")),
        "score_loss": score_loss,
        "winrate_loss": winrate_loss,
        "severity": classify_severity(score_loss),
        "concept_tags": _clean_tags(item.get("concept_tags") or analysis.get("concept_tags")),
        "is_played": move == played_move,
    }


def _candidate_loss(
    item: Mapping[str, Any],
    analysis: Mapping[str, Any],
    direct_key: str,
    metric_key: str,
) -> float:
    if direct_key in item:
        return _float(item.get(direct_key), 0.0)
    root = analysis.get("root")
    if isinstance(root, Mapping) and metric_key in root and metric_key in item:
        return abs(_float(root.get(metric_key), 0.0) - _float(item.get(metric_key), 0.0))
    if direct_key in analysis:
        return _float(analysis.get(direct_key), 0.0)
    return 0.0


def _synthetic_played_candidate(analysis: Mapping[str, Any], played_move: str) -> dict[str, Any]:
    score_loss = _float(analysis.get("score_loss"), 0.0)
    winrate_loss = _float(analysis.get("winrate_loss"), 0.0)
    return {
        "move": played_move,
        "rank": None,
        "score_loss": score_loss,
        "winrate_loss": winrate_loss,
        "severity": classify_severity(score_loss),
        "concept_tags": _clean_tags(analysis.get("concept_tags")),
        "is_played": True,
    }


def _contains_move(candidates: Sequence[Mapping[str, Any]], move: str) -> bool:
    return any(candidate.get("move") == move for candidate in candidates)


def _best_move(candidates: Sequence[Mapping[str, Any]]) -> str | None:
    if not candidates:
        return None
    move = candidates[0].get("move")
    return None if move is None else str(move)


def _played_score_loss(analysis: Mapping[str, Any], played_move: str) -> float:
    if "score_loss" in analysis:
        return _float(analysis.get("score_loss"), 0.0)
    for candidate in _candidate_list(analysis):
        if candidate["move"] == analysis.get("played_move", played_move):
            return _float(candidate.get("score_loss"), 0.0)
    return 0.0


def _played_winrate_loss(analysis: Mapping[str, Any], played_move: str) -> float:
    if "winrate_loss" in analysis:
        return _float(analysis.get("winrate_loss"), 0.0)
    for candidate in _candidate_list(analysis):
        if candidate["move"] == analysis.get("played_move", played_move):
            return _float(candidate.get("winrate_loss"), 0.0)
    return 0.0


def _concept_tags(
    analysis: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> list[str]:
    tags = _clean_tags(analysis.get("concept_tags"))
    for candidate in candidates:
        tags.extend(_clean_tags(candidate.get("concept_tags")))
    return _dedupe(tags)


def _tags_from_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[str]:
    tags: list[str] = []
    for candidate in candidates:
        tags.extend(_clean_tags(candidate.get("concept_tags")))
    return _dedupe(tags)


def _recommended_focus(moments: Sequence[Mapping[str, Any]]) -> list[str]:
    tags: list[str] = []
    for moment in moments:
        tags.extend(_clean_tags(moment.get("concept_tags")))
    return _dedupe(tags)[:5]


def _ownership_regions(ownership: Sequence[Any], board_size: int) -> list[dict[str, Any]]:
    if len(ownership) != board_size * board_size:
        return []
    row_ranges = _third_ranges(board_size)
    col_ranges = _third_ranges(board_size)
    names = [
        ["upper_left", "upper_side", "upper_right"],
        ["left_side", "center", "right_side"],
        ["lower_left", "lower_side", "lower_right"],
    ]
    regions: list[dict[str, Any]] = []
    for row_index, rows in enumerate(row_ranges):
        for col_index, cols in enumerate(col_ranges):
            values = [
                _float(ownership[row * board_size + col], 0.0)
                for row in rows
                for col in cols
            ]
            average = sum(values) / max(1, len(values))
            regions.append(
                {
                    "region": names[row_index][col_index],
                    "owner": _owner_from_average(average),
                    "confidence": round(abs(average), 4),
                    "point_count": len(values),
                }
            )
    return regions


def _third_ranges(board_size: int) -> list[range]:
    first = board_size // 3
    second = (2 * board_size) // 3
    return [range(0, first), range(first, second), range(second, board_size)]


def _owner_from_average(value: float) -> str:
    if value > 0.15:
        return "B"
    if value < -0.15:
        return "W"
    return "contested"


def _move_at(game: dict[str, Any], turn: int) -> dict[str, Any]:
    if not 1 <= turn <= len(game["moves"]):
        raise ValueError(f"turn must be between 1 and {len(game['moves'])}")
    move = game["moves"][turn - 1]
    if not isinstance(move, dict):
        raise ValueError(f"turn {turn} is not a normalized move")
    return move


def _position_fingerprint(game: dict[str, Any], turn: int) -> str:
    return f"sgf:{game['board_size']}:{turn}"


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[int, float, float, str]:
    rank = candidate.get("rank")
    rank_missing = 1 if rank is None else 0
    rank_value = math.inf if rank is None else float(rank)
    score_loss = _float(candidate.get("score_loss"), math.inf)
    move = str(candidate.get("move", ""))
    return rank_missing, rank_value, score_loss, move


def _severity_threshold(severity: str | None) -> int:
    if severity is None:
        return _SEVERITY_ORDER["excellent"]
    return _SEVERITY_ORDER.get(severity, _SEVERITY_ORDER["excellent"])


def _analysis_required_warning() -> dict[str, str]:
    return {
        "code": "ANALYSIS_REQUIRED",
        "message": "KataGo analysis was not available for this teaching payload.",
    }


def _clean_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_values: Sequence[Any] = [value]
    elif isinstance(value, Sequence):
        raw_values = value
    else:
        return []
    return _dedupe(" ".join(str(item).strip().lower().split()) for item in raw_values)


def _dedupe(values: Sequence[str] | Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _normalize_text(value: Any) -> str:
    return str(value).strip()


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _policy_delta(played_policy: float | None, best_policy: float | None) -> float | None:
    if played_policy is None or best_policy is None:
        return None
    return round(played_policy - best_policy, 10)


def _float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result
