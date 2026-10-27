"""Counters and latency histograms for health and the latency targets (PLAN D20 J).

Built for the hot path: a count is one dictionary update, and a timing goes into a histogram of
fixed log-scale buckets (1-2-5 per decade, from 1 µs to 100 s), so recording never allocates and
memory stays constant however long the engine runs. Percentiles are read from the buckets: each is
the upper edge of the bucket the percentile falls in, so a reading is never optimistic.
"""

import bisect
from collections import defaultdict
from dataclasses import dataclass, field

# bucket upper edges in nanoseconds: 1 µs, 2 µs, 5 µs, 10 µs, ... 100 s
EDGES_NS: tuple[int, ...] = tuple(m * 10**e for e in range(3, 11) for m in (1, 2, 5)) + (
    100 * 10**9,
)


@dataclass(slots=True)
class Histogram:
    counts: list[int] = field(default_factory=lambda: [0] * (len(EDGES_NS) + 1))
    total: int = 0
    max_ns: int = 0

    def add(self, value_ns: int) -> None:
        self.counts[bisect.bisect_left(EDGES_NS, value_ns)] += 1
        self.total += 1
        self.max_ns = max(self.max_ns, value_ns)

    def percentile_ns(self, p: float) -> int | None:
        """The upper edge of the bucket holding the ``p``-th percentile (0 < p <= 100); the true
        value is at most this. Values past the last edge report the largest value seen."""
        if not self.total:
            return None
        rank = max(1, -(-self.total * p // 100))  # ceiling, at least the first value
        seen = 0
        for index, count in enumerate(self.counts):
            seen += count
            if seen >= rank:
                return EDGES_NS[index] if index < len(EDGES_NS) else self.max_ns
        return self.max_ns  # pragma: no cover - the loop always reaches the rank


class Metrics:
    def __init__(self) -> None:
        self.counters: defaultdict[str, int] = defaultdict(int)
        self.histograms: defaultdict[str, Histogram] = defaultdict(Histogram)

    def count(self, name: str, by: int = 1) -> None:
        self.counters[name] += by

    def observe(self, name: str, value_ns: int) -> None:
        self.histograms[name].add(max(0, value_ns))

    def snapshot(self) -> dict[str, object]:
        """Counters, and p50/p99/max per histogram, for the health view."""
        timings = {
            name: {
                "count": h.total,
                "p50_ns": h.percentile_ns(50),
                "p99_ns": h.percentile_ns(99),
                "max_ns": h.max_ns,
            }
            for name, h in sorted(self.histograms.items())
        }
        return {"counters": dict(sorted(self.counters.items())), "timings": timings}


class Stages:
    """Times the stages of one item's trip (receive → signal → P&L → alert) against one start, so
    the feed-to-alert latency and each stage's share are both kept."""

    def __init__(self, metrics: Metrics, name: str, start_mono_ns: int) -> None:
        self._metrics = metrics
        self._name = name
        self._start = self._last = start_mono_ns

    def mark(self, stage: str, now_mono_ns: int) -> None:
        self._metrics.observe(f"{self._name}.{stage}_ns", now_mono_ns - self._last)
        self._last = now_mono_ns

    def finish(self, now_mono_ns: int) -> int:
        total = now_mono_ns - self._start
        self._metrics.observe(f"{self._name}.total_ns", total)
        return total
