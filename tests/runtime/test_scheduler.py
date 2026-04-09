from __future__ import annotations

import asyncio
import logging

import pytest

from runtime.scheduler import BackgroundScheduler


@pytest.mark.asyncio
async def test_tick_once_runs_sync_async_handlers_and_isolates_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    scheduler = BackgroundScheduler(
        interval_seconds=1,
        logger=logging.getLogger("tests.scheduler"),
    )
    calls: list[str] = []

    def sync_handler() -> None:
        calls.append("sync")

    async def async_handler() -> None:
        calls.append("async")

    def failing_handler() -> None:
        calls.append("failing")
        raise RuntimeError("boom")

    scheduler.register_handler("sync", sync_handler)
    scheduler.register_handler("async", async_handler)
    scheduler.register_handler("failing", failing_handler)

    with caplog.at_level(logging.ERROR, logger="tests.scheduler"):
        await scheduler.tick_once()

    assert calls == ["sync", "async", "failing"]
    assert "Scheduler handler failed: failing" in caplog.text


@pytest.mark.asyncio
async def test_start_is_idempotent_and_shutdown_is_safe() -> None:
    scheduler = BackgroundScheduler(interval_seconds=3600)

    await scheduler.start()
    first_task = scheduler._task
    await scheduler.start()
    await asyncio.sleep(0)
    await scheduler.shutdown()
    await scheduler.shutdown()

    assert first_task is not None
    assert scheduler._task is None
