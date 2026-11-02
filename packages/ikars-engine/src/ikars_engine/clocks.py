"""The engine's sense of time: the wall clock live, a virtual clock in replay (PLAN D19 G3, D20 A).

Everything in the engine that needs "now" asks an ``EventClock``, never ``time`` directly, so the
same code runs live and in a replay. The virtual clock only moves forward, and only when the replay
releases the next recorded item at that item's received time: nothing downstream can ever see a
time, or an item, from its future.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

NS = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def exact_ns(when: datetime) -> int:
    """An aware time in exact nanoseconds since the epoch, never through a float."""
    since = when - EPOCH
    return (since.days * 86_400 + since.seconds) * NS + since.microseconds * 1_000


def ns_time(ns: int) -> datetime:
    """``exact_ns``'s inverse, to the microsecond (a ``datetime`` holds no finer)."""
    return EPOCH + timedelta(microseconds=ns // 1_000)


class EventClock(Protocol):
    def now_ns(self) -> int: ...

    def monotonic_ns(self) -> int: ...

    async def sleep(self, seconds: float) -> None: ...


class WallClock:
    def now_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ClockWentBackwards(ValueError):
    """A replay asked the virtual clock to move back in time."""


class VirtualClock:
    """Time as the replay has reached it. ``sleep`` yields to other tasks but does not wait: in a
    replay, time moves only as recorded items are released."""

    def __init__(self, start_ns: int = 0) -> None:
        self._now = start_ns

    def now_ns(self) -> int:
        return self._now

    def monotonic_ns(self) -> int:
        return self._now

    def advance_to(self, at_ns: int) -> None:
        if at_ns < self._now:
            raise ClockWentBackwards(f"the replay went back from {self._now} to {at_ns}")
        self._now = at_ns

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(0)
