from __future__ import annotations

import pytest

import runtime.cache as cache_module
from runtime.cache import RuntimeCacheStore, TTLCache


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
