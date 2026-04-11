from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(slots=True)
class CacheItem(Generic[T]):
    value: T
    expires_at: float | None


@dataclass(frozen=True, slots=True)
class SnapshotLatestState:
    author_id: int | None
    author_name: str
    content: str
    attachments: tuple[str, ...]
    timestamp: str


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
        expired_keys = [key for key, item in self._items.items() if item.expires_at is not None and item.expires_at <= now]
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
        self.snapshot_latest_messages: TTLCache[SnapshotLatestState] = TTLCache()
        self.snapshot_message_counts: TTLCache[int] = TTLCache()

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

    def remember_snapshot_state(
        self,
        channel_id: int,
        message_id: int,
        state: SnapshotLatestState,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.snapshot_latest_messages.set(
            self._snapshot_message_key(channel_id, message_id),
            state,
            ttl_seconds=ttl_seconds,
        )

    def get_snapshot_state(
        self,
        channel_id: int,
        message_id: int,
    ) -> SnapshotLatestState | None:
        return self.snapshot_latest_messages.get(self._snapshot_message_key(channel_id, message_id))

    def forget_snapshot_state(self, channel_id: int, message_id: int) -> SnapshotLatestState | None:
        return self.snapshot_latest_messages.pop(self._snapshot_message_key(channel_id, message_id))

    def set_snapshot_message_count(
        self,
        channel_id: int,
        count: int,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.snapshot_message_counts.set(
            self._snapshot_count_key(channel_id),
            count,
            ttl_seconds=ttl_seconds,
        )

    def get_snapshot_message_count(self, channel_id: int, default: int = 0) -> int:
        value = self.snapshot_message_counts.get(self._snapshot_count_key(channel_id))
        return default if value is None else value

    def increment_snapshot_message_count(self, channel_id: int) -> int:
        next_value = self.get_snapshot_message_count(channel_id, default=0) + 1
        self.set_snapshot_message_count(channel_id, next_value)
        return next_value

    def set_snapshot_threshold_flag(
        self,
        channel_id: int,
        flag_name: str,
        value: bool = True,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        self.set_flag(
            self._snapshot_threshold_key(channel_id, flag_name),
            value,
            ttl_seconds=ttl_seconds,
        )

    def get_snapshot_threshold_flag(
        self,
        channel_id: int,
        flag_name: str,
        default: bool = False,
    ) -> bool:
        return self.get_flag(
            self._snapshot_threshold_key(channel_id, flag_name),
            default=default,
        )

    def clear_ticket_snapshot_state(self, channel_id: int) -> int:
        removed_count = 0
        message_prefix = f"snapshot:{channel_id}:"
        threshold_prefix = f"snapshot-threshold:{channel_id}:"
        for key in list(self.snapshot_latest_messages._items.keys()):
            if key.startswith(message_prefix):
                self.snapshot_latest_messages.pop(key)
                removed_count += 1
        if self.snapshot_message_counts.pop(self._snapshot_count_key(channel_id)) is not None:
            removed_count += 1
        for key in list(self.flags._items.keys()):
            if key.startswith(threshold_prefix):
                self.flags.pop(key)
                removed_count += 1
        return removed_count

    def sweep(self) -> int:
        return (
            self.latest_messages.clear_expired()
            + self.flags.clear_expired()
            + self.snapshot_latest_messages.clear_expired()
            + self.snapshot_message_counts.clear_expired()
        )

    @staticmethod
    def _snapshot_message_key(channel_id: int, message_id: int) -> str:
        return f"snapshot:{channel_id}:{message_id}"

    @staticmethod
    def _snapshot_count_key(channel_id: int) -> str:
        return f"snapshot-count:{channel_id}"

    @staticmethod
    def _snapshot_threshold_key(channel_id: int, flag_name: str) -> str:
        return f"snapshot-threshold:{channel_id}:{flag_name}"
