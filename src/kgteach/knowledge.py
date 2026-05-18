"""Knowledge pack registry and query hooks for teaching services."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

KNOWLEDGE_PACKS: dict[str, dict[str, Any]] = {
    "joseki": {
        "name": "joseki",
        "title": "Joseki",
        "description": "Corner pattern references and common local continuations.",
        "hook": "knowledge.joseki.query",
        "tags": ["joseki", "corner", "approach", "pincer", "shimari"],
        "rank_aware": False,
    },
    "principles": {
        "name": "principles",
        "title": "Principles",
        "description": "General go concepts such as shape, sente, direction, and influence.",
        "hook": "knowledge.principles.query",
        "tags": ["shape", "sente", "gote", "thickness", "influence", "direction"],
        "rank_aware": True,
    },
    "tesuji": {
        "name": "tesuji",
        "title": "Tesuji",
        "description": "Tactical patterns, forcing moves, cuts, nets, ladders, and squeezes.",
        "hook": "knowledge.tesuji.query",
        "tags": ["tesuji", "cut", "net", "ladder", "squeeze", "atari"],
        "rank_aware": True,
    },
    "life_death": {
        "name": "life_death",
        "title": "Life and Death",
        "description": "Eyeshape, liberties, semeai, nakade, and local survival questions.",
        "hook": "knowledge.life_death.query",
        "tags": ["life", "death", "eyeshape", "semeai", "liberty", "nakade"],
        "rank_aware": True,
    },
    "endgame": {
        "name": "endgame",
        "title": "Endgame",
        "description": "Yose, sente endgame, reverse sente, count, and boundary plays.",
        "hook": "knowledge.endgame.query",
        "tags": ["endgame", "yose", "count", "reverse sente", "boundary"],
        "rank_aware": True,
    },
    "pedagogy": {
        "name": "pedagogy",
        "title": "Pedagogy",
        "description": "Rank-aware lesson framing, exercise selection, and review sequencing.",
        "hook": "knowledge.pedagogy.query",
        "tags": ["pedagogy", "lesson", "review", "rank", "exercise"],
        "rank_aware": True,
    },
}

__all__ = [
    "KNOWLEDGE_PACKS",
    "get_knowledge_pack",
    "list_knowledge_packs",
    "query_knowledge_hooks",
]


def list_knowledge_packs() -> dict[str, dict[str, Any]]:
    """Return knowledge pack metadata keyed by pack name."""

    return deepcopy(KNOWLEDGE_PACKS)


def registry_payload() -> dict[str, dict[str, Any]]:
    """Return knowledge pack metadata for engine schema output."""

    return list_knowledge_packs()


def get_knowledge_pack(name: str) -> dict[str, Any]:
    """Return metadata for one knowledge pack.

    Raises:
        KeyError: If the pack name is not registered.
    """

    return deepcopy(KNOWLEDGE_PACKS[name])


def query_knowledge_hooks(
    concept_tags: Iterable[Any] | None = None,
    *,
    position_fingerprint: str | Mapping[str, Any] | None = None,
    student_rank: str | None = None,
    packs: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build query hooks that an external knowledge system can resolve."""

    tags = _clean_tags(concept_tags)
    requested_packs = _pack_sequence(tags, packs)
    position_key = _position_key(position_fingerprint)
    queries = [
        {
            "pack": pack_name,
            "hook": KNOWLEDGE_PACKS[pack_name]["hook"],
            "tag": _tag_for_pack(pack_name, tags),
            "query": _query_text(pack_name, tags, position_key),
            "position_fingerprint": position_key,
            "student_rank": student_rank,
        }
        for pack_name in requested_packs
    ]

    return {
        "concept_tags": tags,
        "position_fingerprint": position_key,
        "knowledge_packs": {
            pack_name: deepcopy(KNOWLEDGE_PACKS[pack_name])
            for pack_name in requested_packs
        },
        "knowledge_queries": queries,
        "suggested_followups": [
            _followup_text(query["pack"], query["tag"]) for query in queries
        ],
    }


def _pack_sequence(tags: list[str], packs: Iterable[str] | None) -> list[str]:
    if packs is not None:
        return [
            pack
            for pack in _dedupe(str(item).strip() for item in packs)
            if pack in KNOWLEDGE_PACKS
        ]

    selected: list[str] = []
    for tag in tags:
        selected.append(_pack_for_tag(tag))
    if not selected:
        selected.append("principles")
    return _dedupe(selected)


def _pack_for_tag(tag: str) -> str:
    if any(keyword in tag for keyword in ("joseki", "corner", "approach", "pincer")):
        return "joseki"
    if any(keyword in tag for keyword in ("tesuji", "atari", "ladder", "net", "squeeze")):
        return "tesuji"
    if any(
        keyword in tag
        for keyword in ("life", "death", "eye", "semeai", "liberty", "nakade")
    ):
        return "life_death"
    if any(keyword in tag for keyword in ("endgame", "yose", "count", "boundary")):
        return "endgame"
    if any(keyword in tag for keyword in ("pedagogy", "lesson", "rank", "exercise")):
        return "pedagogy"
    return "principles"


def _tag_for_pack(pack_name: str, tags: list[str]) -> str:
    for tag in tags:
        if _pack_for_tag(tag) == pack_name:
            return tag
    return pack_name


def _query_text(pack_name: str, tags: list[str], position_key: str) -> str:
    tag = _tag_for_pack(pack_name, tags)
    return f"{tag} reference for {position_key}"


def _followup_text(pack_name: str, tag: str) -> str:
    title = KNOWLEDGE_PACKS[pack_name]["title"]
    return f"Review {title} material for {tag}."


def _position_key(position_fingerprint: str | Mapping[str, Any] | None) -> str:
    if position_fingerprint is None:
        return "unknown-position"
    if isinstance(position_fingerprint, Mapping):
        parts = [
            str(position_fingerprint[key])
            for key in ("board_size", "turn", "local_region", "hash")
            if key in position_fingerprint
        ]
        return ":".join(parts) if parts else "unknown-position"
    text = str(position_fingerprint).strip()
    return text or "unknown-position"


def _clean_tags(concept_tags: Iterable[Any] | None) -> list[str]:
    if concept_tags is None:
        return []
    if isinstance(concept_tags, str):
        raw_tags: Iterable[Any] = (concept_tags,)
    else:
        raw_tags = concept_tags
    return _dedupe(" ".join(str(tag).strip().lower().split()) for tag in raw_tags)


def _dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result
