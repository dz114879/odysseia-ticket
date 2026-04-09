from __future__ import annotations

import asyncio

import pytest

from runtime.debounce import DebounceManager


@pytest.mark.asyncio
async def test_schedule_replaces_previous_callback() -> None:
    manager = DebounceManager()
    calls: list[str] = []

    async def callback(*, value: str) -> None:
        calls.append(value)

    manager.schedule("panel", delay_seconds=0.05, callback=callback, value="first")
    await asyncio.sleep(0)
    manager.schedule("panel", delay_seconds=0.01, callback=callback, value="second")

    await asyncio.sleep(0.08)
    await manager.shutdown()

    assert calls == ["second"]


@pytest.mark.asyncio
async def test_cancel_and_shutdown_prevent_callbacks_from_running() -> None:
    manager = DebounceManager()
    calls: list[str] = []

    def callback(*, value: str) -> None:
        calls.append(value)

    manager.schedule("cancelled", delay_seconds=0.05, callback=callback, value="cancelled")
    manager.cancel("cancelled")
    manager.schedule("shutdown", delay_seconds=0.05, callback=callback, value="shutdown")

    await manager.shutdown()
    await asyncio.sleep(0.06)

    assert calls == []
