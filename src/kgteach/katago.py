"""Async KataGo JSON analysis engine adapter."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

StoneList = Sequence[tuple[str, str] | Sequence[str]]


class KataGoProtocolError(RuntimeError):
    """Raised when the KataGo JSON stream cannot be routed or decoded."""


class KataGoTimeoutError(TimeoutError):
    """Raised when KataGo does not complete a request before the timeout."""


@dataclass(slots=True)
class _PendingRequest:
    request_id: str
    future: asyncio.Future[Any]
    expected_turns: list[int] | None
    responses_by_turn: dict[int, dict[str, Any]] = field(default_factory=dict)


def build_analysis_query(
    *,
    request_id: str,
    moves: StoneList,
    initial_stones: StoneList,
    rules: str,
    komi: float,
    board_size: tuple[int, int],
    analyze_turns: Sequence[int],
    max_visits: int,
    include_ownership: bool,
    include_policy: bool,
    human_sl_profile: str | None = None,
) -> dict[str, Any]:
    """Build a KataGo JSON analysis query payload."""

    board_x_size, board_y_size = board_size
    query: dict[str, Any] = {
        "id": request_id,
        "moves": _stone_pairs(moves),
        "initialStones": _stone_pairs(initial_stones),
        "rules": rules,
        "komi": komi,
        "boardXSize": board_x_size,
        "boardYSize": board_y_size,
        "analyzeTurns": [int(turn) for turn in analyze_turns],
        "maxVisits": int(max_visits),
        "includeOwnership": bool(include_ownership),
        "includePolicy": bool(include_policy),
    }
    if human_sl_profile:
        query["overrideSettings"] = {"humanSLProfile": human_sl_profile}
    return query


def build_analysis_query_line(query: Mapping[str, Any]) -> str:
    """Serialize a query payload as a single compact JSON line without newline."""

    return json.dumps(dict(query), ensure_ascii=False, separators=(",", ":"))


class KataGoEngineClient:
    """Async client for a subprocess-like KataGo analysis engine."""

    def __init__(self, process: Any) -> None:
        self._process = process
        self._pending: dict[str, _PendingRequest] = {}
        self._reader_task: asyncio.Task[None] | None = None

    async def analyze(
        self,
        query: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        """Write an analysis query and wait for all requested final turn results."""

        request_id = _request_id(query)
        expected_turns = _expected_turns(query)
        result = await self._request(
            dict(query),
            request_id=request_id,
            expected_turns=expected_turns,
            timeout=timeout,
        )
        if not isinstance(result, list):
            raise KataGoProtocolError("Analysis request returned a non-list result.")
        return result

    async def query_version(
        self,
        *,
        request_id: str = "query_version",
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Query KataGo version metadata with the JSON protocol special action."""

        result = await self._request(
            {"id": request_id, "action": "query_version"},
            request_id=request_id,
            expected_turns=None,
            timeout=timeout,
        )
        if not isinstance(result, dict):
            raise KataGoProtocolError("Version request returned a non-object result.")
        return result

    async def aclose(self) -> None:
        """Cancel the background stdout reader."""

        if self._reader_task is None:
            return
        self._reader_task.cancel()
        try:
            await self._reader_task
        except asyncio.CancelledError:
            pass
        self._reader_task = None

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        request_id: str,
        expected_turns: list[int] | None,
        timeout: float | None,
    ) -> Any:
        self._ensure_reader()
        if request_id in self._pending:
            raise KataGoProtocolError(f"Request id already pending: {request_id}")

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = _PendingRequest(
            request_id=request_id,
            future=future,
            expected_turns=expected_turns,
        )

        try:
            await self._write_json_line(payload)
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise KataGoTimeoutError(f"KataGo request timed out: {request_id}") from exc
        except Exception:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise

    def _ensure_reader(self) -> None:
        if self._reader_task is not None and not self._reader_task.done():
            return
        self._reader_task = asyncio.create_task(self._read_stdout_loop())

    async def _write_json_line(self, payload: Mapping[str, Any]) -> None:
        writer = getattr(self._process, "stdin", None)
        if writer is None:
            raise KataGoProtocolError("KataGo process has no stdin.")

        line = build_analysis_query_line(payload) + "\n"
        write = getattr(writer, "write", None)
        if write is None:
            raise KataGoProtocolError("KataGo stdin has no write method.")

        try:
            write_result = write(line.encode("utf-8"))
        except TypeError:
            write_result = write(line)
        if inspect.isawaitable(write_result):
            await write_result

        drain = getattr(writer, "drain", None)
        if drain is not None:
            drain_result = drain()
            if inspect.isawaitable(drain_result):
                await drain_result

    async def _read_stdout_loop(self) -> None:
        try:
            while True:
                line = await self._readline()
                if line == "":
                    self._fail_all(KataGoProtocolError("KataGo stdout closed."))
                    return
                self._handle_stdout_line(line)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_all(exc)

    async def _readline(self) -> str:
        stdout = getattr(self._process, "stdout", None)
        if stdout is None:
            raise KataGoProtocolError("KataGo process has no stdout.")
        readline = getattr(stdout, "readline", None)
        if readline is None:
            raise KataGoProtocolError("KataGo stdout has no readline method.")

        raw_line = readline()
        if inspect.isawaitable(raw_line):
            raw_line = await raw_line
        if isinstance(raw_line, bytes):
            return raw_line.decode("utf-8")
        if isinstance(raw_line, str):
            return raw_line
        raise KataGoProtocolError("KataGo stdout returned a non-line object.")

    def _handle_stdout_line(self, line: str) -> None:
        if not line.strip():
            return

        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KataGoProtocolError("KataGo emitted invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise KataGoProtocolError("KataGo emitted a non-object JSON payload.")

        request_id = payload.get("id")
        if request_id is None:
            return
        pending = self._pending.get(str(request_id))
        if pending is None:
            return
        if payload.get("isDuringSearch") is True:
            return

        if pending.expected_turns is None:
            self._complete_pending(pending.request_id, payload)
            return

        turn_number = payload.get("turnNumber")
        if not isinstance(turn_number, int):
            self._fail_pending(
                pending.request_id,
                KataGoProtocolError("Analysis response omitted integer turnNumber."),
            )
            return

        pending.responses_by_turn[turn_number] = payload
        if all(turn in pending.responses_by_turn for turn in pending.expected_turns):
            ordered = [pending.responses_by_turn[turn] for turn in pending.expected_turns]
            self._complete_pending(pending.request_id, ordered)

    def _complete_pending(self, request_id: str, result: Any) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is not None and not pending.future.done():
            pending.future.set_result(result)

    def _fail_pending(self, request_id: str, exc: BaseException) -> None:
        pending = self._pending.pop(request_id, None)
        if pending is not None and not pending.future.done():
            pending.future.set_exception(exc)

    def _fail_all(self, exc: BaseException) -> None:
        pending_items = list(self._pending.items())
        self._pending.clear()
        for _, pending in pending_items:
            if not pending.future.done():
                pending.future.set_exception(exc)


def _request_id(query: Mapping[str, Any]) -> str:
    request_id = query.get("id")
    if request_id is None:
        raise KataGoProtocolError("KataGo query is missing id.")
    return str(request_id)


def _expected_turns(query: Mapping[str, Any]) -> list[int]:
    turns = query.get("analyzeTurns")
    if turns is None:
        moves = query.get("moves")
        return [len(moves)] if isinstance(moves, Sequence) else [0]
    if not isinstance(turns, Sequence) or isinstance(turns, str | bytes):
        raise KataGoProtocolError("analyzeTurns must be a sequence of integers.")
    return [int(turn) for turn in turns]


def _stone_pairs(values: StoneList) -> list[list[str]]:
    pairs: list[list[str]] = []
    for value in values:
        if len(value) != 2:
            raise ValueError("KataGo move and stone entries must have two values.")
        color, point = value
        pairs.append([str(color), str(point)])
    return pairs
