from __future__ import annotations

from time import monotonic


class CooldownManager:
    def __init__(self) -> None:
        self._cooldowns: dict[str, float] = {}

    def hit(self, key: str, *, cooldown_seconds: float) -> bool:
        now = monotonic()
        expires_at = self._cooldowns.get(key)
        if expires_at is not None and expires_at > now:
            return True

        self._cooldowns[key] = now + cooldown_seconds
        return False

    def is_active(self, key: str) -> bool:
        expires_at = self._cooldowns.get(key)
        return expires_at is not None and expires_at > monotonic()

    def remaining(self, key: str) -> float:
        expires_at = self._cooldowns.get(key)
        if expires_at is None:
            return 0.0
        return max(0.0, expires_at - monotonic())

    def reset(self, key: str) -> None:
        self._cooldowns.pop(key, None)

    def sweep(self) -> int:
        now = monotonic()
        expired_keys = [key for key, expires_at in self._cooldowns.items() if expires_at <= now]
        for key in expired_keys:
            self._cooldowns.pop(key, None)
        return len(expired_keys)
