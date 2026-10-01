"""Reusable long-lived KataGo analysis runtime with optional disk cache.

The daemon and external workers share this class. ``cache_dir=None`` keeps
every result in memory only, which suits read-only or ephemeral containers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from kgteach.config import KataGoConfig, resolve_katago_config
from kgteach.katago import KataGoEngineClient
from kgteach.runtime import build_analysis_command, close_process

ConfigResolver = Callable[[], KataGoConfig]
ClientFactory = Callable[[KataGoConfig], Awaitable[tuple[Any, KataGoEngineClient]]]

CACHE_KIND = "kgteach.analysis_cache"
CACHE_SCHEMA_VERSION = "0.1.0"


async def spawn_client(config: KataGoConfig) -> tuple[Any, KataGoEngineClient]:
    """Launch a KataGo analysis subprocess and wrap it in a client."""

    command = build_analysis_command(config)
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    return process, KataGoEngineClient(process)


class AnalysisRuntime:
    """Long-lived KataGo analysis runtime plus memory and optional disk cache."""

    def __init__(
        self,
        *,
        cache_dir: Path | None = None,
        config_resolver: ConfigResolver = resolve_katago_config,
        client_factory: ClientFactory | None = None,
    ) -> None:
        """Configure optional cache storage and injectable engine dependencies."""

        self._process: Any | None = None
        self._client: KataGoEngineClient | None = None
        self._config: KataGoConfig | None = None
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_dir = cache_dir
        self._config_resolver = config_resolver
        self._client_factory: ClientFactory = client_factory or spawn_client
        self._lock = asyncio.Lock()

    @property
    def persistent(self) -> bool:
        """Return whether results are also written to disk."""

        return self._cache_dir is not None

    @property
    def cache_items(self) -> int:
        """Return the number of distinct cached analysis results."""

        keys = set(self._cache)
        if self._cache_dir is not None:
            keys |= disk_cache_keys(self._cache_dir)
        return len(keys)

    def cache_summary(self) -> dict[str, Any]:
        """Return cache metrics for status responses."""

        return {
            "cache_items": self.cache_items,
            "memory_items": len(self._cache),
            "disk_items": (
                0 if self._cache_dir is None else len(disk_cache_keys(self._cache_dir))
            ),
            "persistent": self.persistent,
            "cache_dir": None if self._cache_dir is None else str(self._cache_dir),
        }

    @property
    def katago_pid(self) -> int | None:
        """Return the child KataGo process id when available."""

        pid = getattr(self._process, "pid", None)
        return pid if isinstance(pid, int) else None

    async def analyze(
        self,
        query: Mapping[str, Any],
        *,
        timeout: float | None,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Analyze ``query`` with a persistent process, caching by query and config.

        Returns the final responses and whether they came from the cache.
        """

        config = self._config_resolver()
        key = cache_key(query, config)
        cached = self._lookup(key)
        if cached is not None:
            return cached, True
        async with self._lock:
            cached = self._lookup(key)
            if cached is not None:
                return cached, True
            client = await self._client_for_config(config)
            responses = await client.analyze(query, timeout=timeout)
            self._cache[key] = [dict(item) for item in responses]
            if self._cache_dir is not None:
                write_cache_file(self._cache_dir, key, responses)
            return [dict(item) for item in responses], False

    def clear_cache(self) -> int:
        """Drop memory and disk entries, returning how many keys were removed."""

        keys = set(self._cache)
        self._cache.clear()
        if self._cache_dir is not None:
            keys |= disk_cache_keys(self._cache_dir)
            for cache_file in cache_files(self._cache_dir):
                _unlink_quietly(cache_file)
        return len(keys)

    async def close(self) -> None:
        """Close the client and KataGo process if they are running."""

        client, process = self._client, self._process
        self._client = None
        self._process = None
        self._config = None
        if client is not None:
            await client.aclose()
        if process is not None:
            await close_process(process)

    def _lookup(self, key: str) -> list[dict[str, Any]] | None:
        """Return a cached response, loading from disk only when enabled."""

        if key in self._cache:
            return [dict(item) for item in self._cache[key]]
        if self._cache_dir is None:
            return None
        responses = read_cache_file(self._cache_dir, key)
        if responses is None:
            return None
        self._cache[key] = [dict(item) for item in responses]
        return [dict(item) for item in responses]

    async def _client_for_config(self, config: KataGoConfig) -> KataGoEngineClient:
        """Reuse a matching client or replace it for the resolved config."""

        if self._client is not None and self._config == config:
            return self._client
        await self.close()
        self._process, self._client = await self._client_factory(config)
        self._config = config
        return self._client


def cache_key(query: Mapping[str, Any], config: KataGoConfig) -> str:
    """Return the sha256 cache key for ``query`` under ``config``'s file identity."""

    payload = {
        "query": query,
        "config": {
            "binary": _file_identity(config.binary),
            "model": _file_identity(config.model),
            "config": _file_identity(config.config),
            "human_model": _file_identity(config.human_model),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cache_file_path(cache_dir: Path, cache_key: str) -> Path:
    """Return the on-disk path for ``cache_key``; reject non-digest keys."""

    if not is_cache_key(cache_key):
        raise ValueError("cache key must be a sha256 hex digest")
    return cache_dir / f"{cache_key}.json"


def read_cache_file(cache_dir: Path, cache_key: str) -> list[dict[str, Any]] | None:
    """Read cached responses; delete and return ``None`` on any corruption."""

    cache_file = cache_file_path(cache_dir, cache_key)
    try:
        raw_payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError):
        _unlink_quietly(cache_file)
        return None
    if not isinstance(raw_payload, dict):
        _unlink_quietly(cache_file)
        return None
    responses = raw_payload.get("responses")
    if not isinstance(responses, list) or not all(isinstance(item, dict) for item in responses):
        _unlink_quietly(cache_file)
        return None
    return [dict(item) for item in responses]


def write_cache_file(
    cache_dir: Path,
    cache_key: str,
    responses: Sequence[Mapping[str, Any]],
) -> None:
    """Atomically write ``responses`` for ``cache_key``."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_file_path(cache_dir, cache_key)
    temporary_file = cache_file.with_name(f"{cache_file.name}.tmp")
    payload = {
        "kind": CACHE_KIND,
        "schema_version": CACHE_SCHEMA_VERSION,
        "responses": [dict(response) for response in responses],
    }
    temporary_file.write_text(
        json.dumps(payload, sort_keys=True, allow_nan=False), encoding="utf-8"
    )
    os.replace(temporary_file, cache_file)


def disk_cache_keys(cache_dir: Path) -> set[str]:
    """Return every cache key that has a file on disk."""

    return {cache_file.stem for cache_file in cache_files(cache_dir)}


def cache_files(cache_dir: Path) -> list[Path]:
    """Return cache files in ``cache_dir``; an unreadable directory yields none."""

    try:
        return [
            path
            for path in cache_dir.glob("*.json")
            if path.is_file() and is_cache_key(path.stem)
        ]
    except OSError:
        return []


def is_cache_key(value: str) -> bool:
    """Return whether ``value`` looks like a sha256 hex digest."""

    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _unlink_quietly(path: Path) -> None:
    """Remove a cache file if possible, tolerating missing or read-only files."""

    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        return


def _file_identity(path: str | None) -> dict[str, Any] | None:
    """Describe a configured file by path, size and modification time."""

    if path is None:
        return None
    expanded = Path(path).expanduser()
    try:
        stat = expanded.stat()
    except OSError:
        return {"path": str(expanded), "exists": False}
    return {"path": str(expanded), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
