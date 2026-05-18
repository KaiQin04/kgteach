"""Small Go legality engine for validating teaching variations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kgteach.sgf import _COLUMNS

Point = tuple[int, int]
Board = dict[Point, str]


@dataclass(frozen=True, slots=True)
class LegalMove:
    """Result of applying one legal move to a board."""

    player: str
    move: str
    is_pass: bool
    captured: list[str]
    ko_point: str | None


class IllegalMoveError(ValueError):
    """Raised when a move is illegal in the current position."""


def validate_variation(
    game: Mapping[str, Any],
    *,
    turn: int,
    line: Sequence[str],
) -> list[LegalMove]:
    """Validate a variation with captures, suicide, and simple-ko checks.

    Args:
        game: Normalized kgteach game object.
        turn: Main-line turn after which the variation starts.
        line: Candidate moves in GTP coordinates or pass.

    Returns:
        One ``LegalMove`` per variation ply.

    Raises:
        IllegalMoveError: If a move is occupied, suicidal, ko-recapture, or
            outside the board.
    """

    board_size = int(game["board_size"])
    board = _board_after_turn(game, turn)
    ko_point: Point | None = None
    player = _next_player(game, turn)
    results: list[LegalMove] = []

    for ply, raw_move in enumerate(line, start=1):
        move = _normalize_gtp_or_pass(str(raw_move), board_size)
        if move == "pass":
            results.append(
                LegalMove(
                    player=player,
                    move="pass",
                    is_pass=True,
                    captured=[],
                    ko_point=None,
                )
            )
            ko_point = None
            player = _opponent(player)
            continue

        point = gtp_to_point(move, board_size)
        board, captured, ko_point = _play_move(
            board,
            board_size=board_size,
            player=player,
            point=point,
            move=move,
            ply=ply,
            ko_point=ko_point,
        )
        results.append(
            LegalMove(
                player=player,
                move=move,
                is_pass=False,
                captured=[point_to_gtp(item, board_size) for item in captured],
                ko_point=None if ko_point is None else point_to_gtp(ko_point, board_size),
            )
        )
        player = _opponent(player)
    return results


def gtp_to_point(move: str, board_size: int) -> Point:
    """Convert GTP coordinates to a zero-based board point."""

    normalized = _normalize_gtp_or_pass(move, board_size)
    if normalized == "pass":
        raise ValueError("pass does not map to a board point")
    column = normalized[0]
    row = int(normalized[1:])
    return _COLUMNS.index(column), row - 1


def point_to_gtp(point: Point, board_size: int) -> str:
    """Convert a zero-based board point to GTP coordinates."""

    x, y = point
    if not (0 <= x < board_size and 0 <= y < board_size):
        raise ValueError(f"point {point!r} is outside a {board_size}x{board_size} board")
    return f"{_COLUMNS[x]}{y + 1}"


def _board_after_turn(game: Mapping[str, Any], turn: int) -> Board:
    moves = game.get("moves", [])
    if not isinstance(moves, Sequence):
        raise ValueError("game moves must be a sequence")
    if not 0 <= turn <= len(moves):
        raise ValueError(f"turn must be between 0 and {len(moves)}")

    board_size = int(game["board_size"])
    board: Board = {}
    for stone in game.get("initial_stones", []):
        if not isinstance(stone, Mapping) or stone.get("move") == "pass":
            continue
        board[gtp_to_point(str(stone["move"]), board_size)] = str(stone["player"])

    ko_point: Point | None = None
    for index, move in enumerate(moves[:turn], start=1):
        if not isinstance(move, Mapping):
            continue
        player = str(move["player"])
        raw_move = str(move["move"])
        if raw_move == "pass":
            ko_point = None
            continue
        board, _captured, ko_point = _play_move(
            board,
            board_size=board_size,
            player=player,
            point=gtp_to_point(raw_move, board_size),
            move=raw_move,
            ply=index,
            ko_point=ko_point,
        )
    return board


def _next_player(game: Mapping[str, Any], turn: int) -> str:
    moves = game.get("moves", [])
    if isinstance(moves, Sequence) and turn < len(moves):
        next_move = moves[turn]
        if isinstance(next_move, Mapping) and str(next_move.get("player")) in {"B", "W"}:
            return str(next_move["player"])
    if turn > 0 and isinstance(moves, Sequence):
        previous = moves[turn - 1]
        if isinstance(previous, Mapping) and str(previous.get("player")) in {"B", "W"}:
            return _opponent(str(previous["player"]))
    return "B" if turn % 2 == 0 else "W"


def _play_move(
    board: Board,
    *,
    board_size: int,
    player: str,
    point: Point,
    move: str,
    ply: int,
    ko_point: Point | None,
) -> tuple[Board, list[Point], Point | None]:
    if point in board:
        raise IllegalMoveError(f"Move {move} is illegal at ply {ply}: point is occupied.")
    if ko_point is not None and point == ko_point:
        raise IllegalMoveError(f"Move {move} is illegal at ply {ply}: simple ko recapture.")

    next_board = dict(board)
    next_board[point] = player
    captured: list[Point] = []
    opponent = _opponent(player)
    for neighbor in _neighbors(point, board_size):
        if next_board.get(neighbor) != opponent:
            continue
        group = _group(next_board, neighbor, board_size)
        if not _liberties(next_board, group, board_size):
            captured.extend(group)
    for captured_point in captured:
        del next_board[captured_point]

    own_group = _group(next_board, point, board_size)
    if not _liberties(next_board, own_group, board_size):
        raise IllegalMoveError(f"Move {move} is illegal at ply {ply}: suicide.")

    new_ko_point = _ko_point_after_move(
        board=board,
        next_board=next_board,
        placed=point,
        captured=captured,
        board_size=board_size,
    )
    return next_board, captured, new_ko_point


def _ko_point_after_move(
    *,
    board: Board,
    next_board: Board,
    placed: Point,
    captured: Sequence[Point],
    board_size: int,
) -> Point | None:
    if len(captured) != 1:
        return None
    own_group = _group(next_board, placed, board_size)
    if len(own_group) != 1:
        return None
    liberties = _liberties(next_board, own_group, board_size)
    if liberties != {captured[0]}:
        return None
    if board.get(captured[0]) is None:
        return None
    return captured[0]


def _group(board: Board, start: Point, board_size: int) -> set[Point]:
    color = board[start]
    stack = [start]
    visited: set[Point] = set()
    while stack:
        point = stack.pop()
        if point in visited:
            continue
        visited.add(point)
        for neighbor in _neighbors(point, board_size):
            if board.get(neighbor) == color and neighbor not in visited:
                stack.append(neighbor)
    return visited


def _liberties(board: Board, group: Iterable[Point], board_size: int) -> set[Point]:
    liberties: set[Point] = set()
    for point in group:
        for neighbor in _neighbors(point, board_size):
            if neighbor not in board:
                liberties.add(neighbor)
    return liberties


def _neighbors(point: Point, board_size: int) -> list[Point]:
    x, y = point
    candidates = ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
    return [
        (nx, ny)
        for nx, ny in candidates
        if 0 <= nx < board_size and 0 <= ny < board_size
    ]


def _normalize_gtp_or_pass(move: str, board_size: int) -> str:
    normalized = move.strip()
    if normalized.lower() == "pass":
        return "pass"
    if len(normalized) < 2:
        raise IllegalMoveError(f"Invalid coordinate: {move}")
    column = normalized[0].upper()
    if column == "I" or column not in _COLUMNS[:board_size]:
        raise IllegalMoveError(f"Invalid coordinate: {move}")
    try:
        row = int(normalized[1:])
    except ValueError as exc:
        raise IllegalMoveError(f"Invalid coordinate: {move}") from exc
    if not 1 <= row <= board_size:
        raise IllegalMoveError(f"Invalid coordinate: {move}")
    return f"{column}{row}"


def _opponent(player: str) -> str:
    return "W" if player == "B" else "B"
