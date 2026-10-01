"""Tests for KataGo runtime launch and analysis normalization."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from kgteach.analysis import build_query_from_game, normalize_analysis_responses
from kgteach.config import KataGoConfig
from kgteach.contract import ErrorCode
from kgteach.runtime import (
    KataGoRuntimeError,
    analyze_query,
    build_analysis_command,
    check_runtime_ready,
)


class QueueStdout:
    """Async stdout stream backed by a queue."""

    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        """Return the next queued line."""

        return await self.lines.get()

    def put_json(self, payload: Mapping[str, Any]) -> None:
        """Queue one JSON response line."""

        line = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        self.lines.put_nowait(line + b"\n")

    def put_raw(self, line: str) -> None:
        """Queue one raw response line."""

        self.lines.put_nowait(line.encode("utf-8"))


class AutoRespondingStdin:
    """Capture stdin writes and emit matching final KataGo responses."""

    def __init__(self, stdout: QueueStdout) -> None:
        self.stdout = stdout
        self.lines: list[str] = []
        self.closed = False
        self.wait_closed_called = False

    def write(self, data: bytes) -> None:
        """Capture the query line and queue final responses for each turn."""

        line = data.decode("utf-8")
        self.lines.append(line)
        payload = json.loads(line)
        for turn in payload.get("analyzeTurns", []):
            self.stdout.put_json(
                {
                    "id": payload["id"],
                    "turnNumber": turn,
                    "rootInfo": {"visits": 8, "winrate": 0.61},
                    "moveInfos": [{"move": "D4", "visits": 8, "winrate": 0.61}],
                }
            )

    async def drain(self) -> None:
        """Match asyncio StreamWriter shape."""

    def close(self) -> None:
        """Record stdin close."""

        self.closed = True

    async def wait_closed(self) -> None:
        """Record wait_closed calls."""

        self.wait_closed_called = True


class PassiveStdin:
    """Capture writes without emitting stdout responses."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        """Capture bytes written by the client."""

        self.lines.append(data.decode("utf-8"))

    async def drain(self) -> None:
        """Match asyncio StreamWriter shape."""

    def close(self) -> None:
        """Record close."""

        self.closed = True

    async def wait_closed(self) -> None:
        """Match asyncio StreamWriter shape."""


class FakeProcess:
    """Subprocess stand-in with stdin/stdout and termination markers."""

    def __init__(self, stdin: Any, stdout: QueueStdout | None = None) -> None:
        self.stdout = stdout if stdout is not None else QueueStdout()
        self.stdin = stdin
        self.stderr = QueueStdout()
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def terminate(self) -> None:
        """Record graceful termination."""

        self.terminated = True

    def kill(self) -> None:
        """Record forced termination."""

        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        """Mark process as exited."""

        self.wait_calls += 1
        self.returncode = 0 if not self.killed else -9
        return self.returncode


def test_build_analysis_command_uses_argv_and_optional_human_model() -> None:
    """Build KataGo analysis command as an argv list."""

    config = KataGoConfig(
        binary="/opt/bin/katago",
        config="analysis.cfg",
        model="model.bin.gz",
    )

    assert build_analysis_command(config) == [
        "/opt/bin/katago",
        "analysis",
        "-config",
        "analysis.cfg",
        "-model",
        "model.bin.gz",
    ]

    human = KataGoConfig(
        binary="katago",
        config="analysis.cfg",
        model="model.bin.gz",
        human_model="human.bin.gz",
    )

    assert build_analysis_command(human) == [
        "katago",
        "analysis",
        "-config",
        "analysis.cfg",
        "-model",
        "model.bin.gz",
        "-human-model",
        "human.bin.gz",
    ]


def test_build_analysis_command_maps_missing_required_paths() -> None:
    """Raise machine-readable runtime errors for incomplete configs."""

    with pytest.raises(KataGoRuntimeError) as missing_model:
        build_analysis_command(KataGoConfig(config="analysis.cfg"))

    with pytest.raises(KataGoRuntimeError) as missing_config:
        build_analysis_command(KataGoConfig(model="model.bin.gz"))

    assert missing_model.value.code == ErrorCode.MODEL_NOT_FOUND
    assert missing_config.value.code == ErrorCode.CONFIG_NOT_FOUND


def test_check_runtime_ready_reports_files_command_and_capabilities(
    tmp_path: Path,
) -> None:
    """Readiness check reports concrete launch blockers and capabilities."""

    binary = tmp_path / "katago"
    model = tmp_path / "model.bin.gz"
    config_file = tmp_path / "analysis.cfg"
    human_model = tmp_path / "human.bin.gz"
    for path in (binary, model, config_file, human_model):
        path.write_text("", encoding="utf-8")

    ready = check_runtime_ready(
        KataGoConfig(
            binary=str(binary),
            model=str(model),
            config=str(config_file),
            human_model=str(human_model),
        )
    )

    assert ready["ready"] is True
    assert ready["katago_found"] is True
    assert ready["model_found"] is True
    assert ready["config_found"] is True
    assert ready["human_model_found"] is True
    assert ready["command"] == [
        str(binary),
        "analysis",
        "-config",
        str(config_file),
        "-model",
        str(model),
        "-human-model",
        str(human_model),
    ]
    assert ready["capabilities"] == {
        "analyze_game": True,
        "ownership": True,
        "human_policy": True,
        "batch_turns": True,
    }
    assert ready["errors"] == []

    blocked = check_runtime_ready(KataGoConfig(binary="missing-katago"))

    assert blocked["ready"] is False
    assert {error["code"] for error in blocked["errors"]} == {
        ErrorCode.ENGINE_UNAVAILABLE.value,
        ErrorCode.MODEL_NOT_FOUND.value,
        ErrorCode.CONFIG_NOT_FOUND.value,
    }


def test_build_query_from_game_maps_normalized_game_to_katago_query() -> None:
    """Build a KataGo query from normalized SGF game data."""

    game = {
        "board_size": 9,
        "rules": "japanese",
        "komi": 6.5,
        "initial_stones": [{"player": "B", "move": "D4"}],
        "moves": [
            {"turn": 1, "player": "B", "move": "Q16"},
            {"turn": 2, "player": "W", "move": "C3"},
        ],
    }

    query = build_query_from_game(
        game,
        turns=[0, 2],
        visits=128,
        include_ownership=True,
        include_policy=False,
        perspective="black",
        request_id="game-1",
    )

    assert query == {
        "id": "game-1",
        "moves": [["B", "Q16"], ["W", "C3"]],
        "initialStones": [["B", "D4"]],
        "rules": "japanese",
        "komi": 6.5,
        "boardXSize": 9,
        "boardYSize": 9,
        "analyzeTurns": [0, 2],
        "maxVisits": 128,
        "includeOwnership": True,
        "includePolicy": False,
    }


def test_normalize_analysis_responses_maps_root_and_top_moves() -> None:
    """Normalize KataGo rootInfo, moveInfos, and turnNumber fields."""

    response = {
        "id": "analysis-1",
        "turnNumber": 3,
        "rootInfo": {
            "currentPlayer": "W",
            "visits": 200,
            "winrate": 0.25,
            "scoreLead": -3.5,
            "utility": -0.2,
        },
        "moveInfos": [
            {
                "move": "D4",
                "visits": 80,
                "winrate": 0.30,
                "scoreLead": -2.5,
                "policy": 0.21,
                "pv": ["D4", "Q16"],
            },
            {
                "move": "Q16",
                "visits": 60,
                "winrate": 0.20,
                "scoreLead": -4.0,
                "policy": 0.17,
                "pv": ["Q16"],
            },
            {
                "move": "pass",
                "visits": 2,
                "winrate": 0.05,
                "scoreLead": -20.0,
            },
        ],
    }

    normalized = normalize_analysis_responses(
        [response],
        top_n=2,
        perspective="black",
    )

    assert normalized == {
        "perspective": "black",
        "top_n": 2,
        "positions": [
            {
                "position": {
                    "id": "analysis-1",
                    "turn": 3,
                    "turn_number": 3,
                    "current_player": "W",
                },
                "root": {
                    "visits": 200,
                    "winrate": 0.75,
                    "score_lead": 3.5,
                    "utility": -0.2,
                },
                "best_moves": [
                    {
                        "rank": 1,
                        "move": "D4",
                        "visits": 80,
                        "winrate": 0.70,
                        "score_lead": 2.5,
                        "policy": 0.21,
                        "pv": ["D4", "Q16"],
                    },
                    {
                        "rank": 2,
                        "move": "Q16",
                        "visits": 60,
                        "winrate": 0.80,
                        "score_lead": 4.0,
                        "policy": 0.17,
                        "pv": ["Q16"],
                    },
                ],
            }
        ],
    }

    with_raw = normalize_analysis_responses([response], raw=True)

    assert with_raw["positions"][0]["raw"] == response


def test_normalize_analysis_responses_preserves_policy_arrays() -> None:
    """Preserve regular and human policy arrays for rank-aware teaching."""

    normalized = normalize_analysis_responses(
        [
            {
                "id": "analysis-policy",
                "turnNumber": 0,
                "rootInfo": {"currentPlayer": "B"},
                "moveInfos": [],
                "policy": [0.1, None, 0.2],
                "humanPolicy": [0.3, 0.4, None],
            }
        ],
        top_n=0,
    )

    position = normalized["positions"][0]
    assert position["policy"] == [0.1, None, 0.2]
    assert position["human_policy"] == [0.3, 0.4, None]


def test_analyze_query_launches_katago_and_terminates_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Launch KataGo with create_subprocess_exec and always terminate it."""

    asyncio.run(_analyze_query_launches_katago_and_terminates_process(monkeypatch))


async def _analyze_query_launches_katago_and_terminates_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = QueueStdout()
    process = FakeProcess(AutoRespondingStdin(stdout), stdout)
    captured: dict[str, Any] = {}

    async def fake_create_subprocess_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        captured["argv"] = list(argv)
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(
        "kgteach.runtime.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    query = {
        "id": "analysis-1",
        "moves": [],
        "initialStones": [],
        "rules": "japanese",
        "komi": 6.5,
        "boardXSize": 19,
        "boardYSize": 19,
        "analyzeTurns": [0],
        "maxVisits": 16,
        "includeOwnership": False,
        "includePolicy": True,
    }

    responses = await analyze_query(
        query,
        KataGoConfig(binary="katago", model="model.bin.gz", config="analysis.cfg"),
        timeout=1.0,
    )

    assert captured["argv"] == [
        "katago",
        "analysis",
        "-config",
        "analysis.cfg",
        "-model",
        "model.bin.gz",
    ]
    assert captured["kwargs"]["stdin"] is asyncio.subprocess.PIPE
    assert captured["kwargs"]["stdout"] is asyncio.subprocess.PIPE
    assert captured["kwargs"]["stderr"] is asyncio.subprocess.PIPE
    assert "shell" not in captured["kwargs"]
    assert responses[0]["turnNumber"] == 0
    assert process.stdin.closed is True
    assert process.stdin.wait_closed_called is True
    assert process.terminated is True
    assert process.wait_calls == 1


def test_analyze_query_maps_timeout_and_terminates_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map client timeouts to runtime TIMEOUT errors and clean up the process."""

    asyncio.run(_analyze_query_maps_timeout_and_terminates_process(monkeypatch))


async def _analyze_query_maps_timeout_and_terminates_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeProcess(PassiveStdin())

    async def fake_create_subprocess_exec(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(
        "kgteach.runtime.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )
    query = {
        "id": "analysis-timeout",
        "moves": [],
        "initialStones": [],
        "rules": "japanese",
        "komi": 6.5,
        "boardXSize": 19,
        "boardYSize": 19,
        "analyzeTurns": [0],
        "maxVisits": 16,
        "includeOwnership": False,
        "includePolicy": True,
    }

    with pytest.raises(KataGoRuntimeError) as exc_info:
        await analyze_query(
            query,
            KataGoConfig(binary="katago", model="model.bin.gz", config="analysis.cfg"),
            timeout=0.01,
        )

    assert exc_info.value.code == ErrorCode.TIMEOUT
    assert process.terminated is True
    assert process.wait_calls == 1


def test_analyze_query_maps_launch_and_protocol_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Map subprocess launch and JSON protocol errors to stable error codes."""

    asyncio.run(_analyze_query_maps_launch_and_protocol_errors(monkeypatch))


async def _analyze_query_maps_launch_and_protocol_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing_binary(*argv: str, **kwargs: Any) -> FakeProcess:
        raise FileNotFoundError("katago")

    monkeypatch.setattr(
        "kgteach.runtime.asyncio.create_subprocess_exec",
        missing_binary,
    )
    query = {
        "id": "analysis-error",
        "moves": [],
        "initialStones": [],
        "rules": "japanese",
        "komi": 6.5,
        "boardXSize": 19,
        "boardYSize": 19,
        "analyzeTurns": [0],
        "maxVisits": 16,
        "includeOwnership": False,
        "includePolicy": True,
    }

    with pytest.raises(KataGoRuntimeError) as launch_error:
        await analyze_query(
            query,
            KataGoConfig(binary="katago", model="model.bin.gz", config="analysis.cfg"),
            timeout=1.0,
        )

    assert launch_error.value.code == ErrorCode.ENGINE_UNAVAILABLE

    stdout = QueueStdout()
    stdout.put_raw("{bad json}\n")
    process = FakeProcess(PassiveStdin(), stdout)

    async def invalid_json(*argv: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(
        "kgteach.runtime.asyncio.create_subprocess_exec",
        invalid_json,
    )

    with pytest.raises(KataGoRuntimeError) as protocol_error:
        await analyze_query(
            query,
            KataGoConfig(binary="katago", model="model.bin.gz", config="analysis.cfg"),
            timeout=1.0,
        )

    assert protocol_error.value.code == ErrorCode.KATAGO_PROTOCOL_ERROR
    assert process.terminated is True


def _white_to_move_response() -> dict[str, Any]:
    """One final response where White is to move and Black leads by 3.5."""

    return {
        "id": "w-to-move",
        "turnNumber": 3,
        "rootInfo": {"currentPlayer": "W", "visits": 10, "winrate": 0.25, "scoreLead": -3.5},
        "moveInfos": [{"move": "D4", "visits": 8, "winrate": 0.30, "scoreLead": -2.5}],
    }


def test_side_to_move_source_to_black_inverts_for_white() -> None:
    """Default behaviour: KataGo side-to-move numbers flip when White moves."""

    normalized = normalize_analysis_responses([_white_to_move_response()], perspective="black")
    root = normalized["positions"][0]["root"]
    assert root["score_lead"] == 3.5
    assert root["winrate"] == 0.75


def test_black_source_to_black_perspective_keeps_sign() -> None:
    """reportAnalysisWinratesAs=BLACK input must not be flipped a second time."""

    normalized = normalize_analysis_responses(
        [_white_to_move_response()],
        perspective="black",
        source_perspective="black",
    )
    root = normalized["positions"][0]["root"]
    assert root["score_lead"] == -3.5
    assert root["winrate"] == 0.25
    assert normalized["positions"][0]["best_moves"][0]["score_lead"] == -2.5


def test_black_source_to_side_to_move_inverts_for_white() -> None:
    """BLACK numbers shown from the mover's seat flip when White is to move."""

    normalized = normalize_analysis_responses(
        [_white_to_move_response()],
        perspective="side_to_move",
        source_perspective="black",
    )
    root = normalized["positions"][0]["root"]
    assert root["score_lead"] == 3.5
    assert root["winrate"] == 0.75


def test_invalid_source_perspective_is_rejected() -> None:
    """source_perspective shares the perspective validation."""

    with pytest.raises(ValueError, match="perspective must be one of"):
        normalize_analysis_responses([_white_to_move_response()], source_perspective="mover")
