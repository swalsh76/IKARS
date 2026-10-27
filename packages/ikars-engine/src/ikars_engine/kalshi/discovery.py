"""Discovery: the engine's index of Kalshi's markets, kept in step (from KAPS ``discovery.py``).

A **full** cycle (at start, then daily) lists the series (for categories) and every unopened and
open market. An **incremental** cycle (every minute) asks only for markets updated since the
bookmark, which covers new markets, status changes, closings and settlements, and takes a request or
two. Combo markets (``KXMVE``) are never listed. A market that does not fit the model is set aside
and counted, so it can never fail the cycle.

The bookmark (Unix seconds) advances only when a whole cycle has succeeded, and is set two minutes
before the cycle began, so clock and ordering edges cannot hide a change; a failed cycle is retried
from the same point. *Kept from KAPS:* the two cycles, the bookmark rule, rejects counted.
*Changed:* the index lives in memory (with a daily Parquet snapshot) instead of PostgreSQL, since
the engine needs it for the watched set, the drill-down tree and tradability, not for history.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule, fee_schedule
from ikars_core.models import Market

from ikars_engine.clocks import EventClock
from ikars_engine.store import StateStore

BOOKMARK = "discovery.updated_s"
OVERLAP_SECONDS = 120
COMBO_PREFIX = "KXMVE"
log = logging.getLogger("ikars_engine.kalshi.discovery")


@dataclass(frozen=True, slots=True)
class MarketInfo:
    ticker: str
    event_ticker: str
    series_ticker: str
    category: str
    title: str
    status: str
    close_time: datetime | None
    result: str | None
    volume_24h_e2: int  # contracts traded in the last day, in hundredths


def series_of(event_ticker: str) -> str:
    """A series ticker is the event ticker up to its first dash (``KXHIGHNY-26OCT28``)."""
    return event_ticker.split("-", 1)[0]


class MarketIndex:
    def __init__(self) -> None:
        self.markets: dict[str, MarketInfo] = {}
        self.categories: dict[str, str] = {}  # series -> category
        self.fees: dict[str, FeeSchedule | None] = {}  # series -> fee terms (None: not modelled)
        self.rejects = 0
        self.combos_skipped = 0

    def put(self, market: Market) -> None:
        if market.ticker.startswith(COMBO_PREFIX):
            self.combos_skipped += 1
            return
        event = market.event_ticker or ""
        series = series_of(event)
        self.markets[market.ticker] = MarketInfo(
            ticker=market.ticker,
            event_ticker=event,
            series_ticker=series,
            category=self.categories.get(series, ""),
            title=market.title or "",
            status=market.status or "",
            close_time=market.close_time,
            result=market.result,
            volume_24h_e2=market.volume_24h_e2 or 0,
        )

    def open_markets(self) -> list[MarketInfo]:
        return [m for m in self.markets.values() if m.status in ("active", "open")]

    def snapshot(self, path: Path) -> int:
        rows = list(self.markets.values())
        table = pa.table(
            {
                "ticker": [m.ticker for m in rows],
                "event_ticker": [m.event_ticker for m in rows],
                "series_ticker": [m.series_ticker for m in rows],
                "category": [m.category for m in rows],
                "title": [m.title for m in rows],
                "status": [m.status for m in rows],
                "close_time": [m.close_time for m in rows],
                "result": [m.result for m in rows],
                "volume_24h_e2": [m.volume_24h_e2 for m in rows],
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        pq.write_table(table, temporary, compression="zstd")
        temporary.replace(path)
        return len(rows)


class Discovery:
    def __init__(
        self, data: MarketData, index: MarketIndex, state: StateStore, clock: EventClock
    ) -> None:
        self._data = data
        self.index = index
        self._state = state
        self._clock = clock
        self.cycles = 0

    async def full(self) -> int:
        """Series and every unopened and open market; returns how many markets were read."""
        began = self._clock.now_ns() // 1_000_000_000
        for series in await self._data.series_list():
            if series.category:
                self.index.categories[series.ticker] = series.category
            self.index.fees[series.ticker] = fee_schedule(series.fee_type, series.fee_multiplier_e6)
        count = 0
        for status in ("unopened", "open"):
            async for page in self._data.markets(
                status=status, mve_filter="exclude", tolerant=True
            ):
                for market in page.items:
                    self.index.put(market)
                count += len(page.items)
                self.index.rejects += len(page.invalid)
        self._state.set_bookmark(BOOKMARK, began - OVERLAP_SECONDS)
        self.cycles += 1
        log.info("discovery: full cycle", extra={"markets": count, "rejects": self.index.rejects})
        return count

    async def incremental(self) -> int:
        """Markets updated since the bookmark (a full cycle first if there is none)."""
        since = self._state.bookmark(BOOKMARK)
        if since is None:
            return await self.full()
        began = self._clock.now_ns() // 1_000_000_000
        count = 0
        async for page in self._data.markets(
            mve_filter="exclude", tolerant=True, min_updated_ts=since
        ):
            for market in page.items:
                self.index.put(market)
            count += len(page.items)
            self.index.rejects += len(page.invalid)
        self._state.set_bookmark(BOOKMARK, began - OVERLAP_SECONDS)
        self.cycles += 1
        return count
