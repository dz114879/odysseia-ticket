from __future__ import annotations

import pytest

import runtime.locks as locks_module
from runtime.locks import LockManager


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


@pytest.mark.asyncio
async def test_lock_manager_reuses_keys_and_cleans_up_stale_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(10.0)
    monkeypatch.setattr(locks_module, "monotonic", clock)

    manager = LockManager()
    alpha = manager.get_lock("alpha")

    assert manager.get_lock("alpha") is alpha
    assert manager.for_ticket("42") is manager.get_lock("ticket:42")
    assert manager.for_channel(7) is manager.get_lock("channel:7")
    assert manager.for_user(9) is manager.get_lock("user:9")

    async with manager.acquire("beta") as beta_lock:
        assert beta_lock.locked() is True
        clock.value = 100.0
        removed_while_locked = manager.cleanup(stale_after_seconds=50)

    clock.value = 200.0
    removed_after_release = manager.cleanup(stale_after_seconds=50)

    assert removed_while_locked == 4
    assert removed_after_release == 1
    assert "beta" not in manager._locks
