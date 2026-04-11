from __future__ import annotations

import pytest

import runtime.cache as cache_module
from runtime.cache import RuntimeCacheStore, SnapshotLatestState, TTLCache


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_ttl_cache_supports_expiry_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(100.0)
    monkeypatch.setattr(cache_module, "monotonic", clock)

    cache = TTLCache[int]()
    cache.set("short", 1, ttl_seconds=10)
    cache.set("persistent", 2)

    assert cache.get("short") == 1
    assert "short" in cache
    assert len(cache) == 2

    clock.value = 120.0
    assert cache.get("short") is None
    assert "short" not in cache

    cache.set("expired", 3, ttl_seconds=5)
    clock.value = 130.0
    assert cache.clear_expired() == 1
    assert cache.pop("persistent") == 2


def test_runtime_cache_store_tracks_messages_and_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(0.0)
    monkeypatch.setattr(cache_module, "monotonic", clock)

    store = RuntimeCacheStore()
    store.remember_message("message:1", {"content": "hello"}, ttl_seconds=5)
    store.set_flag("flag:1", ttl_seconds=5)

    assert store.latest_messages.get("message:1") == {"content": "hello"}
    assert store.get_flag("flag:1") is True

    clock.value = 10.0
    assert store.get_flag("flag:1") is False
    assert store.sweep() == 1
    assert store.latest_messages.get("message:1") is None


def test_runtime_cache_store_tracks_snapshot_state_counts_and_flags() -> None:
    store = RuntimeCacheStore()
    latest_state = SnapshotLatestState(
        author_id=201,
        author_name="creator",
        content="hello world",
        attachments=("[文件: debug.log, 1KB]",),
        timestamp="2024-01-01T00:00:00+00:00",
    )

    store.remember_snapshot_state(9001, 1001, latest_state)
    store.set_snapshot_message_count(9001, 3)
    store.set_snapshot_threshold_flag(9001, "warn_900")

    assert store.get_snapshot_state(9001, 1001) == latest_state
    assert store.get_snapshot_message_count(9001) == 3
    assert store.increment_snapshot_message_count(9001) == 4
    assert store.get_snapshot_threshold_flag(9001, "warn_900") is True
    assert store.forget_snapshot_state(9001, 1001) == latest_state
    assert store.get_snapshot_state(9001, 1001) is None


def test_runtime_cache_store_can_clear_ticket_snapshot_state() -> None:
    store = RuntimeCacheStore()
    store.remember_snapshot_state(
        9001,
        1001,
        SnapshotLatestState(
            author_id=201,
            author_name="creator",
            content="message 1",
            attachments=(),
            timestamp="2024-01-01T00:00:00+00:00",
        ),
    )
    store.remember_snapshot_state(
        9001,
        1002,
        SnapshotLatestState(
            author_id=202,
            author_name="staff",
            content="message 2",
            attachments=(),
            timestamp="2024-01-01T00:01:00+00:00",
        ),
    )
    store.remember_snapshot_state(
        9002,
        2001,
        SnapshotLatestState(
            author_id=999,
            author_name="other",
            content="unrelated",
            attachments=(),
            timestamp="2024-01-01T00:02:00+00:00",
        ),
    )
    store.set_snapshot_message_count(9001, 2)
    store.set_snapshot_message_count(9002, 1)
    store.set_snapshot_threshold_flag(9001, "warn_900")
    store.set_snapshot_threshold_flag(9002, "warn_900")

    assert store.clear_ticket_snapshot_state(9001) == 4
    assert store.get_snapshot_state(9001, 1001) is None
    assert store.get_snapshot_state(9001, 1002) is None
    assert store.get_snapshot_message_count(9001) == 0
    assert store.get_snapshot_threshold_flag(9001, "warn_900") is False
    assert store.get_snapshot_state(9002, 2001) is not None
    assert store.get_snapshot_message_count(9002) == 1
    assert store.get_snapshot_threshold_flag(9002, "warn_900") is True
