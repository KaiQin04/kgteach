"""Game normalization helpers built on the SGF parser."""

from __future__ import annotations

from typing import Any

from kgteach.sgf import parse_sgf, sgf_to_gtp_coord, warning


def normalize_sgf(text: str) -> dict[str, Any]:
    """Normalize SGF text into kgteach's internal game JSON."""

    parsed = parse_sgf(text)
    root = parsed.nodes[0].properties
    warnings: list[dict[str, Any]] = []

    board_size = _parse_int_property(
        root,
        "SZ",
        default=19,
        warnings=warnings,
        label="board size",
    )
    if board_size is None:
        board_size = 19
    komi = _parse_float_property(root, "KM", warnings=warnings, label="komi")
    handicap = _parse_int_property(root, "HA", default=None, warnings=warnings, label="handicap")
    rules = _first(root, "RU")
    metadata = _metadata(root, handicap)

    if rules is None:
        warnings.append(warning("MISSING_RULES", "SGF has no RU property."))
    if komi is None:
        warnings.append(warning("MISSING_KOMI", "SGF has no KM property."))

    warnings.extend(parsed.warnings)
    initial_stones = _initial_stones(root, board_size, warnings)
    moves = _moves(parsed.nodes[1:], board_size, warnings)

    return {
        "board_size": board_size,
        "rules": rules,
        "komi": komi,
        "initial_stones": initial_stones,
        "moves": moves,
        "metadata": metadata,
        "warnings": warnings,
    }


def inspect_sgf(text: str) -> dict[str, Any]:
    """Return compact SGF metadata without full move details."""

    game = normalize_sgf(text)
    return {
        "board_size": game["board_size"],
        "rules": game["rules"],
        "komi": game["komi"],
        "metadata": game["metadata"],
        "move_count": len(game["moves"]),
        "initial_stone_count": len(game["initial_stones"]),
        "warnings": game["warnings"],
    }


def moves_payload(game: dict[str, Any]) -> dict[str, Any]:
    """Return all moves from a normalized game."""

    return {
        "board_size": game["board_size"],
        "moves": game["moves"],
        "warnings": game["warnings"],
    }


def position_payload(game: dict[str, Any], turn: int) -> dict[str, Any]:
    """Return the position after a given turn."""

    moves = list(game["moves"])
    if not 0 <= turn <= len(moves):
        raise ValueError(f"turn must be between 0 and {len(moves)}")
    played = moves[:turn]
    next_player = "B" if turn % 2 == 0 else "W"
    return {
        "board_size": game["board_size"],
        "turn": turn,
        "next_player": next_player,
        "initial_stones": game["initial_stones"],
        "played_moves": played,
        "warnings": game["warnings"],
    }


def slice_payload(
    game: dict[str, Any],
    *,
    start_turn: int,
    end_turn: int,
) -> dict[str, Any]:
    """Return a 1-based inclusive move slice."""

    moves = list(game["moves"])
    if not 1 <= start_turn <= len(moves):
        raise ValueError(f"start_turn must be between 1 and {len(moves)}")
    if not start_turn <= end_turn <= len(moves):
        raise ValueError(f"end_turn must be between start_turn and {len(moves)}")
    return {
        "board_size": game["board_size"],
        "start_turn": start_turn,
        "end_turn": end_turn,
        "moves": moves[start_turn - 1 : end_turn],
        "warnings": game["warnings"],
    }


def _first(properties: dict[str, list[str]], key: str) -> str | None:
    values = properties.get(key)
    if not values:
        return None
    return values[0]


def _parse_int_property(
    properties: dict[str, list[str]],
    key: str,
    *,
    default: int | None,
    warnings: list[dict[str, Any]],
    label: str,
) -> int | None:
    raw = _first(properties, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        warnings.append(warning("BAD_METADATA", f"Invalid {label}: {raw!r}."))
        return default


def _parse_float_property(
    properties: dict[str, list[str]],
    key: str,
    *,
    warnings: list[dict[str, Any]],
    label: str,
) -> float | None:
    raw = _first(properties, key)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        warnings.append(warning("BAD_METADATA", f"Invalid {label}: {raw!r}."))
        return None


def _metadata(properties: dict[str, list[str]], handicap: int | None) -> dict[str, Any]:
    mapping = {
        "GN": "game_name",
        "PB": "black_player",
        "PW": "white_player",
        "RE": "result",
    }
    metadata: dict[str, Any] = {
        target: value
        for key, target in mapping.items()
        if (value := _first(properties, key)) is not None
    }
    if handicap is not None:
        metadata["handicap"] = handicap
    return metadata


def _initial_stones(
    root: dict[str, list[str]],
    board_size: int,
    warnings: list[dict[str, Any]],
) -> list[dict[str, str]]:
    stones: list[dict[str, str]] = []
    for player, key in (("B", "AB"), ("W", "AW")):
        for coord in root.get(key, []):
            try:
                stones.append({"player": player, "move": sgf_to_gtp_coord(coord, board_size)})
            except ValueError as exc:
                warnings.append(warning("BAD_COORDINATE", str(exc), coordinate=coord))
    return stones


def _moves(
    nodes: list[Any],
    board_size: int,
    warnings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    moves: list[dict[str, Any]] = []
    for node in nodes:
        for player in ("B", "W"):
            values = node.properties.get(player)
            if not values:
                continue
            try:
                move = sgf_to_gtp_coord(values[0], board_size)
            except ValueError as exc:
                warnings.append(warning("BAD_COORDINATE", str(exc), coordinate=values[0]))
                continue
            moves.append(
                {
                    "turn": len(moves) + 1,
                    "player": player,
                    "move": move,
                }
            )
            break
    return moves
