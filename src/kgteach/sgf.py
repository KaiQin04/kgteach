"""Conservative SGF parsing helpers for kgteach."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_COLUMNS = "ABCDEFGHJKLMNOPQRSTUVWXYZ"


@dataclass(slots=True)
class SGFNode:
    """One SGF node with parsed properties."""

    properties: dict[str, list[str]]


@dataclass(slots=True)
class SGFParseResult:
    """Main-line SGF parse result."""

    nodes: list[SGFNode]
    warnings: list[dict[str, str]] = field(default_factory=list)


class SGFParser:
    """Small parser for the main line of a single SGF game tree."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._index = 0
        self._warnings: list[dict[str, str]] = []

    def parse(self) -> SGFParseResult:
        """Parse the root and main-line nodes, skipping variations."""

        self._skip_whitespace()
        self._expect("(")
        nodes: list[SGFNode] = []
        while True:
            self._skip_whitespace()
            char = self._peek()
            if char == ";":
                nodes.append(self._parse_node())
                continue
            if char == "(":
                if not any(item["code"] == "UNSUPPORTED_VARIATION" for item in self._warnings):
                    self._warnings.append(
                        {
                            "code": "UNSUPPORTED_VARIATION",
                            "message": "SGF variations are ignored; only the main line is parsed.",
                        }
                    )
                self._skip_game_tree()
                continue
            if char == ")":
                self._index += 1
                break
            if char == "":
                raise ValueError("unterminated SGF game tree")
            raise ValueError(f"unexpected SGF token {char!r} at index {self._index}")
        if not nodes:
            raise ValueError("SGF contains no nodes")
        return SGFParseResult(nodes=nodes, warnings=self._warnings)

    def _parse_node(self) -> SGFNode:
        self._expect(";")
        properties: dict[str, list[str]] = {}
        while True:
            self._skip_whitespace()
            char = self._peek()
            if char in {"", ";", "(", ")"}:
                break
            key = self._parse_key()
            values = self._parse_values()
            properties.setdefault(key, []).extend(values)
        return SGFNode(properties=properties)

    def _parse_key(self) -> str:
        start = self._index
        while self._peek().isalpha() and self._peek().isupper():
            self._index += 1
        key = self._text[start:self._index]
        if not key:
            raise ValueError(f"expected SGF property at index {self._index}")
        return key

    def _parse_values(self) -> list[str]:
        values: list[str] = []
        while True:
            self._skip_whitespace()
            if self._peek() != "[":
                break
            self._index += 1
            chars: list[str] = []
            while True:
                char = self._peek()
                if char == "":
                    raise ValueError("unterminated SGF property value")
                if char == "\\":
                    self._index += 1
                    escaped = self._peek()
                    if escaped == "":
                        raise ValueError("dangling SGF escape")
                    chars.append(escaped)
                    self._index += 1
                    continue
                if char == "]":
                    self._index += 1
                    break
                chars.append(char)
                self._index += 1
            values.append("".join(chars))
        if not values:
            raise ValueError(f"property at index {self._index} has no value")
        return values

    def _skip_game_tree(self) -> None:
        depth = 0
        while True:
            char = self._peek()
            if char == "":
                raise ValueError("unterminated SGF variation")
            if char == "[":
                self._skip_value()
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    self._index += 1
                    return
            self._index += 1

    def _skip_value(self) -> None:
        self._expect("[")
        while True:
            char = self._peek()
            if char == "":
                raise ValueError("unterminated SGF property value")
            if char == "\\":
                self._index += 2
                continue
            self._index += 1
            if char == "]":
                return

    def _skip_whitespace(self) -> None:
        while self._peek() and self._peek().isspace():
            self._index += 1

    def _expect(self, token: str) -> None:
        self._skip_whitespace()
        if self._peek() != token:
            raise ValueError(f"expected {token!r} at index {self._index}")
        self._index += 1

    def _peek(self) -> str:
        if self._index >= len(self._text):
            return ""
        return self._text[self._index]


def sgf_to_gtp_coord(coord: str, board_size: int) -> str:
    """Convert an SGF coordinate to GTP coordinates."""

    if coord == "":
        return "pass"
    if len(coord) != 2:
        raise ValueError(f"expected two-character SGF coordinate, got {coord!r}")
    x = ord(coord[0]) - ord("a")
    y = ord(coord[1]) - ord("a")
    if not (0 <= x < board_size and 0 <= y < board_size):
        raise ValueError(f"coordinate {coord!r} is outside a {board_size}x{board_size} board")
    if x >= len(_COLUMNS):
        raise ValueError(f"board size {board_size} is too large for GTP coordinates")
    return f"{_COLUMNS[x]}{board_size - y}"


def parse_sgf(text: str) -> SGFParseResult:
    """Parse SGF text into main-line nodes."""

    return SGFParser(text).parse()


def warning(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Build one warning payload."""

    payload: dict[str, Any] = {
        "code": code,
        "message": message,
    }
    payload.update(extra)
    return payload
