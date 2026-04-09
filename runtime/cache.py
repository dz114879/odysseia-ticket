from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class CacheItem(Generic[T]):
    value: T
    expires_at: float | None


class TTLCache(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[str, CacheItem[T]] = {}

    def set(self, key: str, value: T, *, ttl_seconds: float | None = None) -> None:
        expires_at = None if ttl_seconds is None else monotonic() + ttl_seconds
        self._items[key] = CacheItem(value=value, expires_at=expires_at)

    def get(self, key: str, default: T | None = None) -> T | None:
        item = self._items.get(key)
        if item is None:
            return default

        if item.expires_at is not None and item.expires_at <= monotonic():
            self._items.pop(key, None)
            return default

        return item.value

    def pop(self, key: str, default: T | None = None) -> T | None:
        item = self._items.pop(key, None)
        return default if item is None else item.value

    def clear_expired(self) -> int:
        now = monotonic()
        expired_keys = [
            key
            for key, item in self._items.items()
            if item.expires_at is not None and item.expires_at <= now
        ]
        for key in expired_keys:
            self._items.pop(key, None)
        return len(expired_keys)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        return len(self._items)


class RuntimeCacheStore:
    def __init__(self) -> None:
        self.latest_messages: TTLCache[dict] = TTLCache()
        self.flags: TTLCache[bool] = TTLCache()

    def remember_message(
        self,
        key: str,
        message_payload: dict,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.latest_messages.set(key, message_payload, ttl_seconds=ttl_seconds)

    def set_flag(
        self,
        key: str,
        value: bool = True,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.flags.set(key, value, ttl_seconds=ttl_seconds)

    def get_flag(self, key: str, default: bool = False) -> bool:
        value = self.flags.get(key)
        return default if value is None else value

    def sweep(self) -> int:
        return self.latest_messages.clear_expired() + self.flags.clear_expired()
