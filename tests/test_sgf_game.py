"""Tests for SGF parsing and game-facing helpers."""

from __future__ import annotations

import pytest

from kgteach.game import (
    inspect_sgf,
    moves_payload,
    normalize_sgf,
    position_payload,
    slice_payload,
)
from kgteach.sgf import sgf_to_gtp_coord


def test_normalize_sgf_reads_root_metadata_setup_and_main_line() -> None:
    sgf = (
        "(;GM[1]FF[4]SZ[19]KM[6.5]RU[Chinese]HA[2]"
        "GN[Teaching Game]PB[Black Player]PW[White Player]RE[B+R]"
        "AB[pd][dp]AW[dd];B[qp];W[];B[jj])"
    )

    game = normalize_sgf(sgf)

    assert game == {
        "board_size": 19,
        "rules": "Chinese",
        "komi": 6.5,
        "initial_stones": [
            {"player": "B", "move": "Q16"},
            {"player": "B", "move": "D4"},
            {"player": "W", "move": "D16"},
        ],
        "moves": [
            {"turn": 1, "player": "B", "move": "R4"},
            {"turn": 2, "player": "W", "move": "pass"},
            {"turn": 3, "player": "B", "move": "K10"},
        ],
        "metadata": {
            "game_name": "Teaching Game",
            "black_player": "Black Player",
            "white_player": "White Player",
            "result": "B+R",
            "handicap": 2,
        },
        "warnings": [],
    }


@pytest.mark.parametrize(
    ("sgf_coord", "board_size", "expected"),
    [
        ("", 19, "pass"),
        ("aa", 19, "A19"),
        ("ia", 19, "J19"),
        ("jj", 19, "K10"),
        ("ss", 19, "T1"),
        ("aa", 9, "A9"),
        ("ii", 9, "J1"),
    ],
)
def test_sgf_to_gtp_coord_skips_i_column(
    sgf_coord: str,
    board_size: int,
    expected: str,
) -> None:
    assert sgf_to_gtp_coord(sgf_coord, board_size) == expected


def test_inspect_sgf_returns_metadata_without_moves() -> None:
    sgf = (
        "(;SZ[9]KM[0.5]RU[Tromp-Taylor]GN[Nine Board]"
        "PB[Black]PW[White]RE[W+2.5];B[ee];W[dd])"
    )

    payload = inspect_sgf(sgf)

    assert payload == {
        "board_size": 9,
        "rules": "Tromp-Taylor",
        "komi": 0.5,
        "metadata": {
            "game_name": "Nine Board",
            "black_player": "Black",
            "white_player": "White",
            "result": "W+2.5",
        },
        "move_count": 2,
        "initial_stone_count": 0,
        "warnings": [],
    }


def test_game_helpers_return_moves_position_and_slices() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee];W[dd];B[];W[cc])")

    assert moves_payload(game) == {
        "board_size": 9,
        "moves": [
            {"turn": 1, "player": "B", "move": "E5"},
            {"turn": 2, "player": "W", "move": "D6"},
            {"turn": 3, "player": "B", "move": "pass"},
            {"turn": 4, "player": "W", "move": "C7"},
        ],
        "warnings": [],
    }
    assert position_payload(game, 2) == {
        "board_size": 9,
        "turn": 2,
        "next_player": "B",
        "initial_stones": [],
        "played_moves": [
            {"turn": 1, "player": "B", "move": "E5"},
            {"turn": 2, "player": "W", "move": "D6"},
        ],
        "warnings": [],
    }
    assert slice_payload(game, start_turn=2, end_turn=3) == {
        "board_size": 9,
        "start_turn": 2,
        "end_turn": 3,
        "moves": [
            {"turn": 2, "player": "W", "move": "D6"},
            {"turn": 3, "player": "B", "move": "pass"},
        ],
        "warnings": [],
    }


def test_parser_warns_for_missing_metadata_variations_and_unsupported_values() -> None:
    sgf = "(;SZ[bad]KM[abc]HA[x]GN[Warn];B[aa](;W[bb])(;W[cc]))"

    game = normalize_sgf(sgf)

    assert game["board_size"] == 19
    assert game["rules"] is None
    assert game["komi"] is None
    assert game["metadata"] == {"game_name": "Warn"}
    assert game["moves"] == [{"turn": 1, "player": "B", "move": "A19"}]
    assert [warning["code"] for warning in game["warnings"]] == [
        "BAD_METADATA",
        "BAD_METADATA",
        "BAD_METADATA",
        "MISSING_RULES",
        "MISSING_KOMI",
        "UNSUPPORTED_VARIATION",
    ]


def test_parser_warns_and_skips_bad_coordinates() -> None:
    sgf = "(;SZ[9]KM[6.5]RU[Chinese]AB[zz]AW[aa];B[ee];W[toolong])"

    game = normalize_sgf(sgf)

    assert game["initial_stones"] == [{"player": "W", "move": "A9"}]
    assert game["moves"] == [{"turn": 1, "player": "B", "move": "E5"}]
    assert [warning["code"] for warning in game["warnings"]] == [
        "BAD_COORDINATE",
        "BAD_COORDINATE",
    ]


def test_position_and_slice_validate_turn_ranges() -> None:
    game = normalize_sgf("(;SZ[9]KM[6.5]RU[Chinese];B[ee])")

    with pytest.raises(ValueError, match="turn must be between"):
        position_payload(game, 2)
    with pytest.raises(ValueError, match="start_turn must be"):
        slice_payload(game, start_turn=0, end_turn=1)
    with pytest.raises(ValueError, match="end_turn must be"):
        slice_payload(game, start_turn=1, end_turn=2)
