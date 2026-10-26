"""Clock skew against Kalshi.

A signed request carries a timestamp, so a badly skewed local clock makes every request fail.
Kalshi publishes no time endpoint and no tolerance, so the skew is estimated from the HTTP ``Date``
header, which has one-second resolution. Each sample therefore gives an *interval* for the offset,
and intersecting several spaced samples narrows it. Offsets are ``local - server``: positive means
the local clock is ahead.

The WebSocket gives a second, finer source for free: every frame may carry ``sending_ts_ms``.
``FrameDelays`` keeps the smallest ``received - sending`` seen, which is the offset plus the
quickest trip, so it is an upper bound on the offset that tightens as the feed runs.
"""

import statistics
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Literal

DEFAULT_THRESHOLD = 2.0  # seconds; our own choice, Kalshi's tolerance is undocumented

Status = Literal["ok", "skewed", "uncertain"]


class ClockCheckError(Exception):
    """The clock could not be measured (no samples, or no usable ``Date`` header)."""


@dataclass(frozen=True, slots=True)
class ClockSample:
    sent: float  # local epoch seconds just before the request
    received: float  # local epoch seconds just after the response
    server: float  # the earliest server time the response implies (``Date`` plus ``Age``)


@dataclass(frozen=True, slots=True)
class ClockSkew:
    offset: float  # best estimate of local - server, seconds
    uncertainty: float  # half the width of the interval the true offset lies in
    low: float
    high: float
    samples: int
    consistent: bool  # False if the samples' intervals did not overlap
    threshold: float

    @property
    def status(self) -> Status:
        if self.low > self.threshold or self.high < -self.threshold:
            return "skewed"  # even the nearest edge of the interval is beyond the threshold
        if -self.threshold <= self.low and self.high <= self.threshold:
            return "ok"
        return "uncertain"

    @property
    def offset_ms(self) -> int:
        """The offset in whole milliseconds, for :class:`ikars_core.auth.DataSigner`."""
        return round(self.offset * 1000)

    def describe(self) -> str:
        direction = "ahead of" if self.offset >= 0 else "behind"
        quality = "" if self.consistent else " (samples disagreed; low confidence)"
        return (
            f"local clock is {abs(self.offset):.2f}s {direction} Kalshi "
            f"(±{self.uncertainty:.2f}s, {self.samples} samples){quality}: {self.status}"
        )


def parse_http_date(value: str) -> float:
    """An HTTP ``Date`` header as epoch seconds."""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError) as exc:
        raise ClockCheckError(f"unparseable Date header: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def sample_from_response(sent: float, received: float, headers: Mapping[str, str]) -> ClockSample:
    """A sample from a response's headers: its ``Date``, plus its ``Age`` if a cache added one."""
    lowered = {key.lower(): value for key, value in headers.items()}
    if "date" not in lowered:
        raise ClockCheckError("the response has no Date header")
    server = parse_http_date(lowered["date"])
    age = lowered.get("age", "").strip()
    if age.isdigit():
        server += int(age)  # the response is that much older than its Date says
    return ClockSample(sent, received, server)


def estimate_skew(samples: list[ClockSample], threshold: float = DEFAULT_THRESHOLD) -> ClockSkew:
    if not samples:
        raise ClockCheckError("no samples")
    # The server stamped its clock at some local instant in [sent, received], and its true time
    # is in [server, server + 1) because Date is cut to whole seconds.
    intervals = [(s.sent - s.server - 1.0, s.received - s.server) for s in samples]
    low = max(lo for lo, _ in intervals)
    high = min(hi for _, hi in intervals)
    if low <= high:
        return ClockSkew(
            (low + high) / 2, (high - low) / 2, low, high, len(samples), True, threshold
        )
    # inconsistent (for example a cached response): the median, with the widest interval
    mids = [(lo + hi) / 2 for lo, hi in intervals]
    half = max((hi - lo) / 2 for lo, hi in intervals)
    offset = statistics.median(mids)
    return ClockSkew(offset, half, offset - half, offset + half, len(samples), False, threshold)


class FrameDelays:
    """The smallest ``received - sending`` over recent frames: an upper bound on the offset."""

    def __init__(self, window: int = 10_000) -> None:
        self._delays_ms: deque[int] = deque(maxlen=window)

    def add(self, received_ms: int, sending_ts_ms: int) -> None:
        self._delays_ms.append(received_ms - sending_ts_ms)

    @property
    def count(self) -> int:
        return len(self._delays_ms)

    def upper_bound_seconds(self) -> float | None:
        """Offset plus the quickest trip seen, in seconds; None before any frame."""
        return min(self._delays_ms) / 1000 if self._delays_ms else None
