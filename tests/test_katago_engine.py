"""Tests for KataGo config resolution and JSON analysis protocol handling."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from kgteach.config import resolve_katago_config
from kgteach.katago import (
    KataGoEngineClient,
    KataGoTimeoutError,
    build_analysis_query,
    build_analysis_query_line,
)


class FakeStdout:
    """Async stdout reader backed by a queue of JSON lines."""

    def __init__(self) -> None:
        self._lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        """Return the next queued stdout line."""

        return await self._lines.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        """Queue one encoded JSON line."""

        line = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        await self._lines.put(line + b"\n")


class FakeStdin:
    """Subprocess-like stdin writer that captures lines."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self._written = asyncio.Event()

    def write(self, data: bytes) -> None:
        """Capture bytes written by the engine client."""

        self.lines.append(data.decode("utf-8"))
        self._written.set()

    async def drain(self) -> None:
        """Match asyncio stream writer shape."""

    async def next_line(self) -> str:
        """Wait for and return the next captured line."""

        await asyncio.wait_for(self._written.wait(), timeout=1.0)
        return self.lines[-1]


class FakeProcess:
    """Small subprocess stand-in with stdin and stdout attributes."""

    def __init__(self) -> None:
        self.stdin = FakeStdin()
        self.stdout = FakeStdout()


def write_json(path: Path, payload: dict[str, str]) -> None:
    """Write a compact JSON config file for tests."""

    path.write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_katago_config_uses_explicit_env_project_user_default(
    tmp_path: Path,
) -> None:
    """Resolve each config key from the highest available source."""

    user_file = tmp_path / "user.json"
    project_file = tmp_path / "project.json"
    write_json(
        user_file,
        {
            "binary": "user-bin",
            "model": "user-model.bin.gz",
            "config": "user.cfg",
            "human_model": "user-human.bin.gz",
        },
    )
    write_json(
        project_file,
        {
            "binary": "project-bin",
            "model": "project-model.bin.gz",
            "config": "project.cfg",
        },
    )

    resolved = resolve_katago_config(
        binary="explicit-bin",
        env={
            "KGTEACH_KATAGO_BINARY": "env-bin",
            "KGTEACH_KATAGO_MODEL": "env-model.bin.gz",
        },
        project_file=project_file,
        user_file=user_file,
    )

    assert resolved.binary == "explicit-bin"
    assert resolved.model == "env-model.bin.gz"
    assert resolved.config == "project.cfg"
    assert resolved.human_model == "user-human.bin.gz"


def test_resolve_katago_config_has_default_binary() -> None:
    """Use KataGo on PATH when no higher-precedence source is set."""

    resolved = resolve_katago_config(
        env={"KGTEACH_DISABLE_LOCAL_APP_DISCOVERY": "1"},
        project_file=None,
        user_file=None,
    )

    assert resolved.binary == "katago"
    assert resolved.model is None
    assert resolved.config is None
    assert resolved.human_model is None


def test_resolve_katago_config_can_fallback_to_goreview_install(
    tmp_path: Path,
) -> None:
    """Discover the local GoReview KataGo install below lower-priority defaults."""

    binary = (
        tmp_path
        / "Library/Application Support/GoReview/engines/katago/katago"
    )
    model = (
        tmp_path
        / "Library/Application Support/GoReview/models/model.bin.gz"
    )
    human_model = (
        tmp_path
        / "Library/Application Support/GoReview/models/b18c384nbt-humanv0.bin.gz"
    )
    config = (
        tmp_path
        / "Library/Application Support/GoReview/KataGo/analysis-b10c128.cfg"
    )
    for path in (binary, model, human_model, config):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    resolved = resolve_katago_config(
        env={},
        project_file=None,
        user_file=None,
        home=tmp_path,
    )

    assert resolved.binary == str(binary)
    assert resolved.model == str(model)
    assert resolved.human_model == str(human_model)
    assert resolved.config == str(config)


def test_build_analysis_query_line_matches_katago_json_protocol() -> None:
    """Build one JSON-line analysis query with optional human profile settings."""

    query = build_analysis_query(
        request_id="game-7",
        moves=[("B", "Q16"), ("W", "D4")],
        initial_stones=[("B", "K10")],
        rules="japanese",
        komi=6.5,
        board_size=(13, 13),
        analyze_turns=[0, 2],
        max_visits=64,
        include_ownership=True,
        include_policy=False,
        human_sl_profile="10k",
    )

    assert query == {
        "id": "game-7",
        "moves": [["B", "Q16"], ["W", "D4"]],
        "initialStones": [["B", "K10"]],
        "rules": "japanese",
        "komi": 6.5,
        "boardXSize": 13,
        "boardYSize": 13,
        "analyzeTurns": [0, 2],
        "maxVisits": 64,
        "includeOwnership": True,
        "includePolicy": False,
        "overrideSettings": {"humanSLProfile": "10k"},
    }

    line = build_analysis_query_line(query)

    assert "\n" not in line
    assert json.loads(line) == query


def test_client_aggregates_out_of_order_turns_and_ignores_progress() -> None:
    """Collect final responses by requested turn order, regardless of arrival order."""

    asyncio.run(_client_aggregates_out_of_order_turns_and_ignores_progress())


async def _client_aggregates_out_of_order_turns_and_ignores_progress() -> None:
    process = FakeProcess()
    client = KataGoEngineClient(process)
    query = build_analysis_query(
        request_id="analysis-1",
        moves=[("B", "Q16"), ("W", "D4")],
        initial_stones=[],
        rules="tromp-taylor",
        komi=7.5,
        board_size=(19, 19),
        analyze_turns=[0, 2],
        max_visits=32,
        include_ownership=True,
        include_policy=True,
    )

    try:
        task = asyncio.create_task(client.analyze(query, timeout=1.0))
        written = await process.stdin.next_line()
        await process.stdout.send_json(
            {
                "id": "analysis-1",
                "turnNumber": 2,
                "isDuringSearch": True,
                "rootInfo": {"visits": 4},
            }
        )
        await process.stdout.send_json(
            {
                "id": "unrelated",
                "turnNumber": 0,
                "rootInfo": {"scoreLead": 99.0},
            }
        )
        await process.stdout.send_json(
            {
                "id": "analysis-1",
                "turnNumber": 2,
                "rootInfo": {"scoreLead": -1.5},
            }
        )
        await process.stdout.send_json(
            {
                "id": "analysis-1",
                "turnNumber": 0,
                "rootInfo": {"scoreLead": 0.25},
            }
        )

        responses = await task
    finally:
        await client.aclose()

    assert json.loads(written)["id"] == "analysis-1"
    assert [item["turnNumber"] for item in responses] == [0, 2]
    assert [item["rootInfo"]["scoreLead"] for item in responses] == [0.25, -1.5]


def test_client_times_out_when_final_response_never_arrives() -> None:
    """Raise a timeout when pending requested turns do not complete."""

    asyncio.run(_client_times_out_when_final_response_never_arrives())


async def _client_times_out_when_final_response_never_arrives() -> None:
    process = FakeProcess()
    client = KataGoEngineClient(process)
    query = build_analysis_query(
        request_id="analysis-timeout",
        moves=[],
        initial_stones=[],
        rules="chinese",
        komi=7.5,
        board_size=(19, 19),
        analyze_turns=[0],
        max_visits=16,
        include_ownership=False,
        include_policy=False,
    )

    try:
        with pytest.raises(KataGoTimeoutError):
            await client.analyze(query, timeout=0.01)
    finally:
        await client.aclose()


def test_client_sends_query_version_special_action() -> None:
    """Use KataGo's query_version action and return its response by id."""

    asyncio.run(_client_sends_query_version_special_action())


async def _client_sends_query_version_special_action() -> None:
    process = FakeProcess()
    client = KataGoEngineClient(process)

    try:
        task = asyncio.create_task(
            client.query_version(request_id="version-1", timeout=1.0)
        )
        written = await process.stdin.next_line()
        await process.stdout.send_json(
            {
                "id": "version-1",
                "name": "KataGo",
                "version": "1.15.3",
            }
        )
        response = await task
    finally:
        await client.aclose()

    assert json.loads(written) == {"id": "version-1", "action": "query_version"}
    assert response["version"] == "1.15.3"
