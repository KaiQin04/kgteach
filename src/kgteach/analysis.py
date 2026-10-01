"""KataGo analysis query construction and response normalization."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from kgteach.katago import build_analysis_query

_PERSPECTIVES = {"side_to_move", "black", "white"}


def build_query_from_game(
    game: Mapping[str, Any],
    *,
    request_id: str,
    turns: Sequence[int],
    visits: int = 800,
    include_ownership: bool = False,
    include_policy: bool = True,
    perspective: str = "side_to_move",
    human_sl_profile: str | None = None,
) -> dict[str, Any]:
    """Build a KataGo query from a normalized game."""

    _validate_perspective(perspective)
    moves = _move_pairs(game.get("moves", []))
    turn_numbers = [int(turn) for turn in turns]
    _validate_turns(turn_numbers, max_turn=len(moves))
    if visits < 1:
        raise ValueError("visits must be a positive integer.")

    board_size = _board_size(game["board_size"])
    return build_analysis_query(
        request_id=request_id,
        moves=moves,
        initial_stones=_stone_pairs(game.get("initial_stones", [])),
        rules=str(game.get("rules") or "japanese"),
        komi=float(game.get("komi") or 6.5),
        board_size=board_size,
        analyze_turns=turn_numbers,
        max_visits=visits,
        include_ownership=include_ownership,
        include_policy=include_policy,
        human_sl_profile=human_sl_profile,
    )


def normalize_analysis_responses(
    responses: Sequence[Mapping[str, Any]],
    *,
    top_n: int = 5,
    perspective: str = "side_to_move",
    raw: bool = False,
    source_perspective: str = "side_to_move",
) -> dict[str, Any]:
    """Normalize responses, converting from the declared source perspective.

    The source defaults to KataGo's side-to-move reporting. Configs using
    ``reportAnalysisWinratesAs = BLACK`` or ``WHITE`` must declare that colour
    so winrates and scores are converted exactly once.
    """

    _validate_perspective(perspective)
    _validate_perspective(source_perspective)
    if top_n < 0:
        raise ValueError("top_n must be non-negative.")
    return {
        "perspective": perspective,
        "top_n": int(top_n),
        "positions": [
            _normalize_one_response(
                response,
                top_n=top_n,
                perspective=perspective,
                source_perspective=source_perspective,
                raw=raw,
            )
            for response in responses
        ],
    }


def _normalize_one_response(
    response: Mapping[str, Any],
    *,
    top_n: int,
    perspective: str,
    source_perspective: str,
    raw: bool,
) -> dict[str, Any]:
    root_info = response.get("rootInfo", {})
    if not isinstance(root_info, Mapping):
        root_info = {}
    move_infos = response.get("moveInfos", [])
    if not isinstance(move_infos, list):
        move_infos = []
    turn_number = _optional_int(response.get("turnNumber"), default=0) or 0
    current_player = _current_player(root_info.get("currentPlayer"), turn_number)

    normalized_moves = [
        _normalize_move_info(
            index,
            item,
            perspective=perspective,
            current_player=current_player,
            source_perspective=source_perspective,
        )
        for index, item in enumerate(move_infos[:top_n], start=1)
        if isinstance(item, Mapping)
    ]
    payload: dict[str, Any] = {
        "position": {
            "turn": turn_number,
            "turn_number": turn_number,
            "current_player": current_player,
        },
        "root": _normalize_root_info(
            root_info,
            perspective=perspective,
            current_player=current_player,
            source_perspective=source_perspective,
        ),
        "best_moves": normalized_moves,
    }
    if response.get("id") is not None:
        payload["position"]["id"] = str(response["id"])
    ownership = response.get("ownership")
    if isinstance(ownership, list):
        payload["ownership"] = [_optional_float(value) for value in ownership]
    policy = response.get("policy")
    if isinstance(policy, list):
        payload["policy"] = [_optional_float(value) for value in policy]
    human_policy = response.get("humanPolicy")
    if isinstance(human_policy, list):
        payload["human_policy"] = [_optional_float(value) for value in human_policy]
    if raw:
        payload["raw"] = dict(response)
    return payload


def _normalize_root_info(
    root_info: Mapping[str, Any],
    *,
    perspective: str,
    current_player: str,
    source_perspective: str,
) -> dict[str, Any]:
    root: dict[str, Any] = {}
    if (visits := _optional_int(root_info.get("visits"))) is not None:
        root["visits"] = visits
    if (winrate := _optional_float(root_info.get("winrate"))) is not None:
        root["winrate"] = _winrate_for_perspective(
            winrate,
            perspective=perspective,
            current_player=current_player,
            source_perspective=source_perspective,
        )
    if (score_lead := _optional_float(root_info.get("scoreLead"))) is not None:
        root["score_lead"] = _score_for_perspective(
            score_lead,
            perspective=perspective,
            current_player=current_player,
            source_perspective=source_perspective,
        )
    if (utility := _optional_float(root_info.get("utility"))) is not None:
        root["utility"] = utility
    return root


def _normalize_move_info(
    rank: int,
    item: Mapping[str, Any],
    *,
    perspective: str,
    current_player: str,
    source_perspective: str,
) -> dict[str, Any]:
    move: dict[str, Any] = {
        "rank": rank,
        "move": str(item.get("move", "pass")),
    }
    if (visits := _optional_int(item.get("visits"))) is not None:
        move["visits"] = visits
    if (winrate := _optional_float(item.get("winrate"))) is not None:
        move["winrate"] = _winrate_for_perspective(
            winrate,
            perspective=perspective,
            current_player=current_player,
            source_perspective=source_perspective,
        )
    if (score_lead := _optional_float(item.get("scoreLead"))) is not None:
        move["score_lead"] = _score_for_perspective(
            score_lead,
            perspective=perspective,
            current_player=current_player,
            source_perspective=source_perspective,
        )
    if (policy := _optional_float(item.get("policy"))) is not None:
        move["policy"] = policy
    if (prior := _optional_float(item.get("prior"))) is not None:
        move["prior"] = prior
    pv = item.get("pv", [])
    if isinstance(pv, list):
        move["pv"] = [str(value) for value in pv if isinstance(value, str)]
    return move


def _move_pairs(raw_moves: object) -> list[tuple[str, str]]:
    if not isinstance(raw_moves, Sequence) or isinstance(raw_moves, str | bytes):
        raise ValueError("game moves must be a sequence.")
    return [_stone_pair(move) for move in raw_moves]


def _stone_pairs(raw_stones: object) -> list[tuple[str, str]]:
    if not isinstance(raw_stones, Sequence) or isinstance(raw_stones, str | bytes):
        raise ValueError("game initial_stones must be a sequence.")
    return [_stone_pair(stone) for stone in raw_stones]


def _stone_pair(item: object) -> tuple[str, str]:
    if isinstance(item, Mapping):
        return str(item["player"]), str(item["move"])
    if isinstance(item, Sequence) and not isinstance(item, str | bytes) and len(item) == 2:
        return str(item[0]), str(item[1])
    raise ValueError("stones and moves must contain player/move pairs.")


def _board_size(raw_size: Any) -> tuple[int, int]:
    if (
        isinstance(raw_size, Sequence)
        and not isinstance(raw_size, str | bytes)
        and len(raw_size) == 2
    ):
        return int(raw_size[0]), int(raw_size[1])
    size = int(raw_size)
    return size, size


def _validate_turns(turns: Sequence[int], *, max_turn: int) -> None:
    for turn in turns:
        if turn < 0 or turn > max_turn:
            raise ValueError(f"turn must be between 0 and {max_turn}: {turn}")


def _validate_perspective(perspective: str) -> None:
    if perspective not in _PERSPECTIVES:
        allowed = ", ".join(sorted(_PERSPECTIVES))
        raise ValueError(f"perspective must be one of: {allowed}")


def _current_player(value: object, turn_number: int) -> str:
    if isinstance(value, str) and value.upper() in {"B", "W"}:
        return value.upper()
    return "B" if turn_number % 2 == 0 else "W"


def _winrate_for_perspective(
    winrate: float,
    *,
    perspective: str,
    current_player: str,
    source_perspective: str = "side_to_move",
) -> float:
    if _invert_for_perspective(perspective, current_player, source_perspective):
        return round(1.0 - winrate, 10)
    return winrate


def _score_for_perspective(
    score_lead: float,
    *,
    perspective: str,
    current_player: str,
    source_perspective: str = "side_to_move",
) -> float:
    if _invert_for_perspective(perspective, current_player, source_perspective):
        return -score_lead
    return score_lead


def _absolute_color(perspective: str, current_player: str) -> str:
    """Resolve a perspective name to the colour whose seat it describes."""

    if perspective == "side_to_move":
        return current_player
    return "B" if perspective == "black" else "W"


def _invert_for_perspective(
    perspective: str,
    current_player: str,
    source_perspective: str = "side_to_move",
) -> bool:
    """Return whether raw numbers must flip to reach the target perspective."""

    return _absolute_color(perspective, current_player) != _absolute_color(
        source_perspective, current_player
    )


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
