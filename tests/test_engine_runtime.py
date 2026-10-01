"""Tests for the reusable long-lived analysis runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kgteach import engine_runtime
from kgteach.config import KataGoConfig
from kgteach.engine_runtime import AnalysisRuntime


class FakeClient:
    """Analysis client that records calls and can block until released."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        """Initialize recorded responses and call tracking."""

        self.responses = responses
        self.calls = 0
        self.closed = False
        self.gate: asyncio.Event | None = None

    async def analyze(
        self, query: dict[str, Any], *, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """Return fake responses after any configured gate opens."""

        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return [dict(item) for item in self.responses]

    async def aclose(self) -> None:
        """Record client shutdown."""

        self.closed = True


class FakeProcess:
    """Minimal subprocess stand-in."""

    pid = 4321
    returncode: int | None = None
    stdin = None

    def terminate(self) -> None:
        """Mark the fake subprocess as terminated."""

        self.returncode = 0

    async def wait(self) -> int:
        """Return the successful fake subprocess exit code."""

        return 0


CONFIG = KataGoConfig(binary="katago", model="model.bin.gz", config="analysis.cfg")
RESPONSES = [{"id": "q", "turnNumber": 0, "rootInfo": {"visits": 1}, "moveInfos": []}]


def make_runtime(
    *, cache_dir: Path | None, client: FakeClient
) -> tuple[AnalysisRuntime, list[int]]:
    """Build a runtime with a fake client and record factory calls."""

    spawns: list[int] = []

    async def factory(config: KataGoConfig) -> tuple[Any, Any]:
        """Create a fake process for the expected config."""

        assert config == CONFIG
        spawns.append(1)
        return FakeProcess(), client

    runtime = AnalysisRuntime(
        cache_dir=cache_dir, config_resolver=lambda: CONFIG, client_factory=factory
    )
    return runtime, spawns


def test_memory_only_runtime_never_touches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise memory operations while all disk cache helpers are forbidden."""

    def forbidden_disk_call(*args: Any, **kwargs: Any) -> Any:
        """Fail if a memory-only runtime calls any disk cache helper."""

        raise AssertionError("Memory-only runtime accessed disk cache")

    for name in ("read_cache_file", "write_cache_file", "disk_cache_keys", "cache_files"):
        monkeypatch.setattr(engine_runtime, name, forbidden_disk_call)

    client = FakeClient(RESPONSES)
    runtime, spawns = make_runtime(cache_dir=None, client=client)

    first, hit1 = asyncio.run(runtime.analyze({"id": "q"}, timeout=1.0))
    second, hit2 = asyncio.run(runtime.analyze({"id": "q"}, timeout=1.0))

    assert first == RESPONSES and second == RESPONSES
    assert (hit1, hit2) == (False, True)
    assert client.calls == 1
    assert runtime.persistent is False
    assert runtime.cache_summary() == {
        "cache_items": 1,
        "memory_items": 1,
        "disk_items": 0,
        "persistent": False,
        "cache_dir": None,
    }
    assert runtime.clear_cache() == 1
    assert runtime.cache_items == 0
    assert list(tmp_path.iterdir()) == []
    assert runtime.katago_pid == 4321
    assert spawns == [1]


def test_disk_runtime_reads_cache_before_engine(tmp_path: Path) -> None:
    """Reuse a disk entry without creating an engine process."""

    client = FakeClient(RESPONSES)
    runtime, spawns = make_runtime(cache_dir=tmp_path, client=client)
    key = engine_runtime.cache_key({"id": "q"}, CONFIG)
    engine_runtime.write_cache_file(tmp_path, key, RESPONSES)

    responses, hit = asyncio.run(runtime.analyze({"id": "q"}, timeout=1.0))

    assert responses == RESPONSES and hit is True
    assert client.calls == 0 and spawns == []
    assert runtime.cache_summary()["persistent"] is True
    assert runtime.cache_summary()["disk_items"] == 1


def test_concurrent_identical_queries_hit_engine_once() -> None:
    """Deduplicate identical in-flight queries under the runtime lock."""

    client = FakeClient(RESPONSES)
    runtime, _ = make_runtime(cache_dir=None, client=client)

    async def scenario() -> tuple[bool, bool]:
        """Schedule both queries before releasing the fake engine."""

        client.gate = asyncio.Event()
        first = asyncio.create_task(runtime.analyze({"id": "q"}, timeout=1.0))
        second = asyncio.create_task(runtime.analyze({"id": "q"}, timeout=1.0))
        await asyncio.sleep(0)
        client.gate.set()
        (_, hit1), (_, hit2) = await asyncio.gather(first, second)
        return hit1, hit2

    hits = asyncio.run(scenario())

    assert client.calls == 1
    assert sorted(hits) == [False, True]


def test_analyze_after_close_recreates_client() -> None:
    """Recreate the engine after explicitly closing the runtime."""

    client = FakeClient(RESPONSES)
    runtime, spawns = make_runtime(cache_dir=None, client=client)

    async def scenario() -> None:
        """Close between two distinct requests and check process state."""

        await runtime.analyze({"id": "a"}, timeout=1.0)
        await runtime.close()
        assert client.closed is True
        assert runtime.katago_pid is None
        await runtime.analyze({"id": "b"}, timeout=1.0)

    asyncio.run(scenario())

    assert spawns == [1, 1]
    assert client.calls == 2


def test_cache_key_changes_with_config_identity(tmp_path: Path) -> None:
    """Separate model identities and reject unsafe cache filenames."""

    other = KataGoConfig(binary="katago", model="other.bin.gz", config="analysis.cfg")

    assert engine_runtime.cache_key({"id": "q"}, CONFIG) != engine_runtime.cache_key(
        {"id": "q"}, other
    )
    with pytest.raises(ValueError, match="cache key"):
        engine_runtime.cache_file_path(tmp_path, "nope")
