from __future__ import annotations

import pytest

import runtime.cooldowns as cooldowns_module
from runtime.cooldowns import CooldownManager


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_cooldown_manager_hit_remaining_reset_and_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock(10.0)
    monkeypatch.setattr(cooldowns_module, "monotonic", clock)

    manager = CooldownManager()

    assert manager.hit("ticket:1", cooldown_seconds=5) is False
    assert manager.is_active("ticket:1") is True
    assert manager.remaining("ticket:1") == pytest.approx(5.0)

    clock.value = 12.0
    assert manager.hit("ticket:1", cooldown_seconds=5) is True
    assert manager.remaining("ticket:1") == pytest.approx(3.0)

    manager.reset("ticket:1")
    assert manager.is_active("ticket:1") is False
    assert manager.remaining("ticket:1") == pytest.approx(0.0)

    manager.hit("ticket:2", cooldown_seconds=1)
    clock.value = 20.0
    assert manager.sweep() == 1
    assert manager.is_active("ticket:2") is False
