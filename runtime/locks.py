from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from typing import AsyncIterator


@dataclass(slots=True)
class LockEntry:
    lock: asyncio.Lock
    last_used_at: float


class LockManager:
    def __init__(self) -> None:
        self._locks: dict[str, LockEntry] = {}

    def get_lock(self, key: str) -> asyncio.Lock:
        entry = self._locks.get(key)
        now = monotonic()
        if entry is None:
            entry = LockEntry(lock=asyncio.Lock(), last_used_at=now)
            self._locks[key] = entry
        else:
            entry.last_used_at = now
        return entry.lock

    def for_ticket(self, ticket_id: str) -> asyncio.Lock:
        return self.get_lock(f"ticket:{ticket_id}")

    def for_channel(self, channel_id: int) -> asyncio.Lock:
        return self.get_lock(f"channel:{channel_id}")

    def for_user(self, user_id: int) -> asyncio.Lock:
        return self.get_lock(f"user:{user_id}")

    @asynccontextmanager
    async def acquire(self, key: str) -> AsyncIterator[asyncio.Lock]:
        lock = self.get_lock(key)
        await lock.acquire()
        try:
            yield lock
        finally:
            entry = self._locks.get(key)
            if entry is not None:
                entry.last_used_at = monotonic()
            lock.release()

    def cleanup(self, *, stale_after_seconds: float = 3600) -> int:
        now = monotonic()
        removable_keys = [key for key, entry in self._locks.items() if not entry.lock.locked() and now - entry.last_used_at >= stale_after_seconds]
        for key in removable_keys:
            self._locks.pop(key, None)
        return len(removable_keys)
