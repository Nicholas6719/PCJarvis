"""A tiny async pub/sub. Every subsystem publishes here; the UI subscribes.
Keeps audio, brain, and interface completely decoupled."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger("jarvis.bus")

Handler = Callable[[dict], Any | Awaitable[Any]]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the main loop so audio callbacks on other threads can publish."""
        self._loop = loop

    def on(self, event: str, handler: Handler) -> None:
        self._subs[event].append(handler)

    async def emit(self, event: str, **payload: Any) -> None:
        for handler in (*self._subs.get(event, ()), *self._subs.get("*", ())):
            try:
                result = handler({"event": event, **payload})
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("handler for %r failed", event)

    def emit_threadsafe(self, event: str, **payload: Any) -> None:
        """Publish from a non-async thread (e.g. the sounddevice callback)."""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self.emit(event, **payload), self._loop)


BUS = EventBus()
