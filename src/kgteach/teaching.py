"""Teaching heuristics and knowledge hooks for normalized KataGo analysis."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, TypeAlias

Severity: TypeAlias = Literal["excellent", "good", "inaccuracy", "mistake", "blunder"]


def classify_severity(score_loss: float) -> Severity:
    """Classify a move by score loss thresholds measured in points."""

    loss = max(0.0, _as_float(score_loss))
    if loss < 0.5:
        return "excellent"
    if loss < 1.5:
        return "good"
    if loss < 3.0:
        return "inaccuracy"
    if loss < 7.0:
        return "mistake"
    return "blunder"


def teaching_priority(
    *,
    score_loss: float,
    winrate_loss: float,
    level_appropriateness: float,
    concept_clarity: float,
    recurrence_count: int,
) -> float:
    """Return a deterministic 0..1 teaching priority from normalized signals."""

    score_component = _clamp(_as_float(score_loss) / 7.0)
    winrate_component = _clamp(_as_float(winrate_loss) / 0.25)
    level_component = _clamp(_as_float(level_appropriateness))
    clarity_component = _clamp(_as_float(concept_clarity))
    recurrence_component = _clamp(_as_float(recurrence_count) / 4.0)

    priority = (
        0.35 * score_component
        + 0.25 * winrate_component
        + 0.15 * level_component
        + 0.15 * clarity_component
        + 0.10 * recurrence_component
    )
    return round(_clamp(priority), 6)


def generate_knowledge_hooks(
    *,
    concept_tags: Iterable[str] | None,
    position_fingerprint: str | None,
    knowledge_refs: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build knowledge retrieval hooks for a teaching moment."""

    position_key = position_fingerprint or "unknown-position"
    tags = _unique_concept_tags(concept_tags)
    queries: list[dict[str, str]] = []
    followups: list[str] = []

    for tag in tags:
        if _is_joseki_tag(tag):
            queries.append(
                {
                    "kind": "joseki",
                    "tag": tag,
                    "query": f"{tag} reference for {position_key}",
                }
            )
            followups.append(f"Review joseki alternatives for {tag} at {position_key}.")
            continue

        queries.append(
            {
                "kind": "principle",
                "tag": tag,
                "query": f"{tag} principle for {position_key}",
            }
        )
        followups.append(f"Practice the {tag} principle in a nearby position.")

    return {
        "concept_tags": tags,
        "position_fingerprint": position_key,
        "knowledge_queries": queries,
        "knowledge_refs": [dict(ref) for ref in knowledge_refs or ()],
        "suggested_followups": followups,
    }


def compare_candidate_moves(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Compare candidate moves from a normalized analysis-like mapping."""

    played_move = _optional_string(_first_present(analysis, "played_move", "playedMove"))
    candidates = [
        _normalize_candidate(candidate, played_move)
        for candidate in _extract_candidate_mappings(analysis)
    ]

    played = _find_played_candidate(candidates, played_move)
    if played_move is not None and played is None:
        played = _synthetic_played_candidate(analysis, played_move)
        candidates.append(played)

    best = min(candidates, key=_candidate_order_key, default=None)
    return {
        "best_move": None if best is None else best["move"],
        "played_move": played_move,
        "played": played,
        "candidates": candidates,
    }


def summarize_move_explanation(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize one played move with severity, priority, comparison, and hooks."""

    comparison = compare_candidate_moves(analysis)
    played = comparison["played"]
    if not isinstance(played, Mapping):
        played = _synthetic_played_candidate(analysis, "unknown")

    raw_played = _find_raw_played_candidate(analysis, _optional_string(played.get("move")))
    severity = _optional_string(played.get("severity")) or classify_severity(0.0)
    concept_tags = _unique_concept_tags(played.get("concept_tags"))
    priority = teaching_priority(
        score_loss=_as_float(played.get("score_loss")),
        winrate_loss=_as_float(played.get("winrate_loss")),
        level_appropriateness=_as_float(
            _first_present(raw_played, "level_appropriateness", "levelAppropriateness"),
            default=0.5,
        ),
        concept_clarity=_as_float(
            _first_present(raw_played, "concept_clarity", "conceptClarity"),
            default=0.5,
        ),
        recurrence_count=int(
            _as_float(_first_present(raw_played, "recurrence_count", "recurrenceCount"))
        ),
    )
    hooks = generate_knowledge_hooks(
        concept_tags=concept_tags,
        position_fingerprint=_optional_string(analysis.get("position_fingerprint")),
        knowledge_refs=_extract_refs(analysis.get("knowledge_refs")),
    )

    return {
        "turn": analysis.get("turn"),
        "headline": _headline(_optional_string(played.get("move")) or "unknown", severity),
        "played": dict(played),
        "severity": severity,
        "teaching_priority": priority,
        "comparison": comparison,
        "knowledge_hooks": hooks,
    }


def _normalize_candidate(
    candidate: Mapping[str, Any],
    played_move: str | None,
) -> dict[str, Any]:
    move = _optional_string(_first_present(candidate, "move", "moveCoords", "move_coords"))
    score_loss = _as_float(_first_present(candidate, "score_loss", "scoreLoss"))
    winrate_loss = _as_float(_first_present(candidate, "winrate_loss", "winrateLoss"))
    is_played = bool(move is not None and played_move is not None and move == played_move)

    return {
        "move": move,
        "rank": _optional_int(_first_present(candidate, "rank", "order")),
        "score_loss": score_loss,
        "winrate_loss": winrate_loss,
        "severity": classify_severity(score_loss),
        "concept_tags": _unique_concept_tags(candidate.get("concept_tags")),
        "is_played": is_played,
    }


def _synthetic_played_candidate(analysis: Mapping[str, Any], played_move: str) -> dict[str, Any]:
    score_loss = _as_float(_first_present(analysis, "score_loss", "scoreLoss"))
    winrate_loss = _as_float(_first_present(analysis, "winrate_loss", "winrateLoss"))
    return {
        "move": played_move,
        "rank": None,
        "score_loss": score_loss,
        "winrate_loss": winrate_loss,
        "severity": classify_severity(score_loss),
        "concept_tags": _unique_concept_tags(analysis.get("concept_tags")),
        "is_played": True,
    }


def _extract_candidate_mappings(analysis: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_candidates = _first_present(analysis, "candidates", "moveInfos", "moves")
    if not isinstance(raw_candidates, Sequence) or isinstance(raw_candidates, str):
        return []
    return [item for item in raw_candidates if isinstance(item, Mapping)]


def _find_played_candidate(
    candidates: Sequence[Mapping[str, Any]],
    played_move: str | None,
) -> Mapping[str, Any] | None:
    if played_move is None:
        return None
    for candidate in candidates:
        if candidate.get("move") == played_move:
            return candidate
    return None


def _find_raw_played_candidate(
    analysis: Mapping[str, Any],
    played_move: str | None,
) -> Mapping[str, Any]:
    if played_move is None:
        return {}
    for candidate in _extract_candidate_mappings(analysis):
        move = _optional_string(_first_present(candidate, "move", "moveCoords", "move_coords"))
        if move == played_move:
            return candidate
    return analysis


def _candidate_order_key(candidate: Mapping[str, Any]) -> tuple[int, float, float, str]:
    rank = _optional_int(candidate.get("rank"))
    rank_missing = 1 if rank is None else 0
    rank_value = math.inf if rank is None else float(rank)
    score_loss = _as_float(candidate.get("score_loss"))
    move = _optional_string(candidate.get("move")) or ""
    return (rank_missing, rank_value, score_loss, move)


def _extract_refs(value: Any) -> Sequence[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return ()
    return [item for item in value if isinstance(item, Mapping)]


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _unique_concept_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    raw_tags: Iterable[Any]
    if isinstance(tags, str):
        raw_tags = (tags,)
    elif isinstance(tags, Iterable):
        raw_tags = tags
    else:
        return []

    cleaned_tags: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        clean_tag = " ".join(str(tag).strip().lower().split())
        if clean_tag and clean_tag not in seen:
            cleaned_tags.append(clean_tag)
            seen.add(clean_tag)
    return cleaned_tags


def _is_joseki_tag(tag: str) -> bool:
    return "joseki" in tag


def _headline(move: str, severity: str) -> str:
    article = "an" if severity in {"excellent", "inaccuracy"} else "a"
    return f"{move} is {article} {severity} worth reviewing."


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(result):
        return default
    return result


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return min(upper, max(lower, value))
