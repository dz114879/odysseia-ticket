from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any


DebouncedCallback = Callable[..., Awaitable[None] | None]


class DebounceManager:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(__name__)
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(
        self,
        key: str,
        *,
        delay_seconds: float,
        callback: DebouncedCallback,
        **callback_kwargs: Any,
    ) -> asyncio.Task[None]:
        self.cancel(key)

        async def runner() -> None:
            try:
                await asyncio.sleep(delay_seconds)
                result = callback(**callback_kwargs)
                if inspect.isawaitable(result):
                    await result
            except asyncio.CancelledError:
                return
            except Exception:
                self._logger.exception("Debounced callback failed: %s", key)
            finally:
                if self._tasks.get(key) is task:
                    self._tasks.pop(key, None)

        task = asyncio.create_task(runner(), name=f"debounce:{key}")
        self._tasks[key] = task
        return task

    def cancel(self, key: str) -> None:
        task = self._tasks.pop(key, None)
        if task is not None and not task.done():
            task.cancel()

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
