from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from time import monotonic
from collections.abc import Awaitable, Callable


ScheduledCallback = Callable[[], Awaitable[None] | None]


@dataclass(slots=True)
class ScheduledHandler:
    name: str
    callback: ScheduledCallback


class BackgroundScheduler:
    def __init__(
        self,
        *,
        interval_seconds: float,
        logger: logging.Logger | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._handlers: list[ScheduledHandler] = []
        self._task: asyncio.Task[None] | None = None

    @property
    def handler_names(self) -> list[str]:
        return [handler.name for handler in self._handlers]

    def register_handler(self, name: str, callback: ScheduledCallback) -> None:
        self._handlers.append(ScheduledHandler(name=name, callback=callback))

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop(), name="background-scheduler")

    async def tick_once(self) -> None:
        for handler in list(self._handlers):
            started_at = monotonic()
            try:
                result = handler.callback()
                if inspect.isawaitable(result):
                    await result
                elapsed = monotonic() - started_at
                self.logger.debug(
                    "Scheduler handler completed: %s (%.3fs)",
                    handler.name,
                    elapsed,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Scheduler handler failed: %s", handler.name)

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run_loop(self) -> None:
        self.logger.info(
            "Background scheduler started. interval=%ss handlers=%s",
            self.interval_seconds,
            self.handler_names,
        )
        try:
            while True:
                cycle_started_at = monotonic()
                await self.tick_once()
                elapsed = monotonic() - cycle_started_at
                await asyncio.sleep(max(0.0, self.interval_seconds - elapsed))
        except asyncio.CancelledError:
            self.logger.info("Background scheduler stopped.")
            raise
