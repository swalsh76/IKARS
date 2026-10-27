"""The clock against Kalshi's, for the signers and the health view (PLAN D19 G10).

Two sources from ``ikars_core.clock``: HTTP ``Date`` samples, which give an interval for the offset
(one-second resolution, narrowed by intersecting several), and the WebSocket frames'
``sending_ts_ms``, whose smallest delay is an upper bound that tightens as the feed runs. The
monitor keeps both and reports one offset: the estimate from the samples when there are any.
M1.4b feeds it live samples; nothing here touches the network.
"""

from collections import deque

from ikars_core.clock import (
    DEFAULT_THRESHOLD,
    ClockSample,
    ClockSkew,
    FrameDelays,
    estimate_skew,
)


class SkewMonitor:
    def __init__(self, keep: int = 20, threshold: float = DEFAULT_THRESHOLD) -> None:
        self._samples: deque[ClockSample] = deque(maxlen=keep)
        self._frames = FrameDelays()
        self._threshold = threshold

    def add_sample(self, sample: ClockSample) -> None:
        self._samples.append(sample)

    def add_frame(self, received_ms: int, sending_ts_ms: int) -> None:
        self._frames.add(received_ms, sending_ts_ms)

    def estimate(self) -> ClockSkew | None:
        if not self._samples:
            return None
        return estimate_skew(list(self._samples), self._threshold)

    @property
    def offset_ms(self) -> int:
        """Local minus Kalshi, in milliseconds (what the signers subtract); 0 until measured."""
        skew = self.estimate()
        return skew.offset_ms if skew is not None else 0

    def report(self) -> dict[str, object]:
        skew = self.estimate()
        bound = self._frames.upper_bound_seconds()
        return {
            "status": skew.status if skew is not None else "unmeasured",
            "offset_ms": skew.offset_ms if skew is not None else None,
            "samples": len(self._samples),
            "frame_bound_ms": None if bound is None else round(bound * 1000),
            "frames": self._frames.count,
        }
