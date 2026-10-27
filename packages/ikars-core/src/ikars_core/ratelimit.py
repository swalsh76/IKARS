"""Client-side rate limiting that mirrors Kalshi's token-bucket model.

Kalshi budgets *tokens* per second (Basic tier: 200 read, 100 write) and most requests cost 10.
Exceeding the budget returns a bare HTTP 429 with no ``Retry-After`` and no cooldown, so the
client paces itself. The bucket starts full and waiters are served first come, first served.
The clock and the sleep are injected so the logic is tested without real waiting.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

_EPSILON = 1e-9  # float slack: a computed sleep can leave the bucket ~1e-15 short


def backoff(step: int, base: float, cap: float) -> float:
    """The capped exponential wait before retry ``step`` (0 first): ``base``, then doubling, at most
    ``cap``. Every retry loop in IKARS waits this way; each adds its own jitter if it wants any."""
    return min(cap, base * 2.0**step)


@dataclass(slots=True)
class BucketStats:
    """What the bucket has done, for the downloader's progress and the rate-limit measurement."""

    acquired: int = 0
    spent: float = 0.0  # tokens
    waits: int = 0
    waited_seconds: float = 0.0


class TokenBucket:
    def __init__(
        self,
        rate: float,
        capacity: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate <= 0 or capacity <= 0:
            raise ValueError("rate and capacity must be positive")
        self._rate = rate
        self._capacity = capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = capacity
        self._updated = clock()
        self._lock = asyncio.Lock()
        self.stats = BucketStats()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    def set_rate(self, rate: float, capacity: float | None = None) -> None:
        """Change the refill rate (and optionally the capacity) from now on.

        Tokens earned so far are kept, up to the new capacity. Used by the downloader to slow
        down after a 429 and to creep back up afterwards.
        """
        if rate <= 0 or (capacity is not None and capacity <= 0):
            raise ValueError("rate and capacity must be positive")
        self._refill()  # settle what was earned at the old rate
        self._rate = rate
        if capacity is not None:
            self._capacity = capacity
            self._tokens = min(self._tokens, capacity)

    @property
    def rate(self) -> float:
        return self._rate

    async def acquire(self, cost: float) -> None:
        """Wait until ``cost`` tokens are available, then spend them."""
        if cost <= 0 or cost > self._capacity:
            raise ValueError(f"cost must be in (0, {self._capacity}]")
        async with self._lock:
            self._refill()
            while self._tokens < cost - _EPSILON:
                delay = (cost - self._tokens) / self._rate
                self.stats.waits += 1
                self.stats.waited_seconds += delay
                await self._sleep(delay)
                self._refill()
            self._tokens = max(0.0, self._tokens - cost)
            self.stats.acquired += 1
            self.stats.spent += cost
