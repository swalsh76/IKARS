"""One input interface, live or replayed (PLAN D19 G3, D20 A).

The engine reads ``Item``s from a ``Source`` and never knows which kind it has:

- ``LiveSource``: feeds and plugins put items into it as they arrive (stamped with the wall
  clock's received time); the engine takes them in arrival order.
- ``replay.RecordingReplay`` (M1.13): reads the recorder's own files, merges them in received-time
  order, and releases each item only after moving the virtual clock to that item's received time,
  so a replay can never show anything before IKARS had it. Ties go by channel, then file and row
  order, so a replay is deterministic.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Protocol

from ikars_engine.model import Item


class Source(Protocol):
    def items(self) -> AsyncIterator[Item]: ...


class LiveSource:
    """Items pushed by live feeds and plugins, taken in arrival order until ``close``."""

    _END = object()

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: asyncio.Queue[object] = asyncio.Queue(maxsize)

    async def put(self, item: Item) -> None:
        await self._queue.put(item)

    async def close(self) -> None:
        await self._queue.put(self._END)

    def end_now(self) -> None:
        """End the stream at once, even when the queue is full (at shutdown: what was not read is
        dropped, never waited for)."""
        while True:
            try:
                self._queue.put_nowait(self._END)
                return
            except asyncio.QueueFull:
                self._queue.get_nowait()

    async def items(self) -> AsyncIterator[Item]:
        while True:
            entry = await self._queue.get()
            if entry is self._END:
                return
            if not isinstance(entry, Item):
                raise TypeError(f"a live source carries items, not {type(entry).__name__}")
            yield entry
