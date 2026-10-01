"""Daemon lifecycle tests."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest


def daemon_module() -> Any:
    """Import the daemon module inside tests so missing APIs fail as RED."""

    return importlib.import_module("kgteach.daemon")


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Return whether a predicate becomes true before the timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def assert_not_running_status(status: dict[str, object]) -> None:
    """Assert the stable shape for a stopped daemon status payload."""

    assert status == {
        "running": False,
        "pid": None,
        "katago_pid": None,
        "uptime_seconds": 0,
        "active_requests": 0,
        "queued_requests": 0,
        "cache_items": 0,
    }


def test_default_state_dir_is_user_friendly() -> None:
    daemon = daemon_module()

    state_dir = daemon.default_state_dir()

    assert isinstance(state_dir, Path)
    assert state_dir.is_absolute()
    assert "kgteach" in state_dir.name


def test_status_reports_not_running_without_state(tmp_path: Path) -> None:
    daemon = daemon_module()

    status = daemon.status(tmp_path)

    assert_not_running_status(status)


def test_start_daemon_launches_background_process_and_is_idempotent(
    tmp_path: Path,
) -> None:
    daemon = daemon_module()

    started = daemon.start_daemon(tmp_path)

    try:
        assert started["running"] is True
        assert isinstance(started["pid"], int)
        assert started["pid"] != os.getpid()
        assert started["katago_pid"] is None
        assert started["active_requests"] == 0
        assert started["queued_requests"] == 0
        assert started["cache_items"] == 0

        assert wait_until(lambda: daemon.status(tmp_path)["running"] is True)
        first_pid = started["pid"]

        second = daemon.start_daemon(tmp_path)

        assert second["running"] is True
        assert second["pid"] == first_pid
    finally:
        daemon.stop_daemon(tmp_path)


def test_daemon_socket_handles_ping_and_reports_socket(tmp_path: Path) -> None:
    daemon = daemon_module()
    daemon.start_daemon(tmp_path)

    try:
        assert wait_until(lambda: daemon.socket_file_path(tmp_path).exists())
        response = daemon.request_daemon({"action": "ping"}, tmp_path, timeout=1.0)
        status = daemon.status(tmp_path)

        assert response["ok"] is True
        assert response["data"]["pong"] is True
        assert status["running"] is True
        assert status["socket_path"] == str(daemon.socket_file_path(tmp_path))
    finally:
        daemon.stop_daemon(tmp_path)


def test_daemon_cache_files_roundtrip_and_reject_invalid_payload(tmp_path: Path) -> None:
    daemon = daemon_module()
    cache_key = "a" * 64
    responses = [
        {
            "id": "cached",
            "turnNumber": 0,
            "rootInfo": {"visits": 1},
            "moveInfos": [],
        }
    ]

    daemon._write_cache_file(tmp_path, cache_key, responses)

    assert daemon._read_cache_file(tmp_path, cache_key) == responses
    assert daemon._disk_cache_keys(tmp_path) == {cache_key}

    cache_file = daemon._cache_file_path(tmp_path, cache_key)
    cache_file.write_text('{"responses": "bad"}', encoding="utf-8")

    assert daemon._read_cache_file(tmp_path, cache_key) is None
    assert not cache_file.exists()

    with pytest.raises(ValueError, match="cache key"):
        daemon._cache_file_path(tmp_path, "not-a-key")


def test_daemon_runtime_uses_persistent_cache_before_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daemon = daemon_module()
    cache_key = "b" * 64
    responses = [
        {
            "id": "cached",
            "turnNumber": 0,
            "rootInfo": {"visits": 1},
            "moveInfos": [],
        }
    ]
    daemon._write_cache_file(tmp_path, cache_key, responses)
    runtime = daemon._DaemonRuntime(cache_dir=tmp_path)
    from kgteach import engine_runtime

    monkeypatch.setattr(engine_runtime, "cache_key", lambda _query, _config: cache_key)

    cached_responses, cache_hit = asyncio.run(runtime.analyze({"id": "query"}, timeout=0.1))

    assert cached_responses == responses
    assert cache_hit is True
    assert runtime.cache_items == 1
    assert runtime.cache_summary()["disk_items"] == 1
    assert runtime.cache_summary()["memory_items"] == 1
    assert runtime.cache_summary()["persistent"] is True
    assert runtime.clear_cache() == 1
    assert runtime.cache_items == 0
    assert not daemon._cache_file_path(tmp_path, cache_key).exists()


def test_stop_daemon_terminates_and_cleans_state(tmp_path: Path) -> None:
    daemon = daemon_module()
    started = daemon.start_daemon(tmp_path)
    pid = started["pid"]

    stopped = daemon.stop_daemon(tmp_path)

    assert_not_running_status(stopped)
    assert wait_until(lambda: daemon.status(tmp_path)["running"] is False)
    assert not daemon.state_file_path(tmp_path).exists()
    assert pid is not None


def test_stop_daemon_is_noop_when_not_running(tmp_path: Path) -> None:
    daemon = daemon_module()

    stopped = daemon.stop_daemon(tmp_path)

    assert_not_running_status(stopped)
    assert not daemon.state_file_path(tmp_path).exists()


def test_status_cleans_stale_pid_state(tmp_path: Path) -> None:
    daemon = daemon_module()
    state_file = daemon.state_file_path(tmp_path)
    stale_pid = 999_999_999
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "pid": stale_pid,
                "katago_pid": None,
                "started_at": time.time() - 30,
                "updated_at": time.time() - 10,
                "active_requests": 0,
                "queued_requests": 0,
                "cache_items": 0,
            }
        ),
        encoding="utf-8",
    )

    status = daemon.status(tmp_path)

    assert_not_running_status(status)
    assert not state_file.exists()


def test_start_daemon_replaces_stale_pid_state(tmp_path: Path) -> None:
    daemon = daemon_module()
    state_file = daemon.state_file_path(tmp_path)
    stale_pid = 999_999_999
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "pid": stale_pid,
                "katago_pid": None,
                "started_at": time.time() - 30,
                "updated_at": time.time() - 10,
                "active_requests": 0,
                "queued_requests": 0,
                "cache_items": 0,
            }
        ),
        encoding="utf-8",
    )

    started = daemon.start_daemon(tmp_path)

    try:
        assert started["running"] is True
        assert started["pid"] != stale_pid
    finally:
        daemon.stop_daemon(tmp_path)
