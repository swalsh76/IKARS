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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule, fee_schedule
from ikars_core.markets import is_combo, series_ticker_of
from ikars_core.models import Market

from ikars_engine.clocks import EventClock
from ikars_engine.store import StateStore

BOOKMARK = "discovery.updated_s"
OVERLAP_SECONDS = 120
OPEN_STATUSES = frozenset({"active", "open"})  # Kalshi's REST and WebSocket names for trading
CLOSED_KEPT_NS = 3 * 86_400 * 1_000_000_000  # a settled market stays in the index this long
SETTLED_STATUSES = frozenset({"settled", "finalized"})  # paid out: nobody holds it any more
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
    # the NWS climate report its rules name, such as "CLINYC" (M2.2: weather markets settle on The
    # Weather Company's value from that government station); None when the rules name none
    climate_code: str | None = None
    # the ticker and title in lower case, made once: the markets search reads it on every request
    # (review 1.R2 item 5)
    search: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", f"{self.ticker}\n{self.title}".lower())


def _long_settled(
    status: str, close_time: datetime | None, now_ns: int, keep_ns: int = CLOSED_KEPT_NS
) -> bool:
    """Settled (paid out, so held by nobody) and closed more than ``keep_ns`` ago. A closed market
    waiting for its result stays: the owner may hold it."""
    return (
        status in SETTLED_STATUSES
        and close_time is not None
        and now_ns - int(close_time.timestamp()) * 1_000_000_000 > keep_ns
    )


_CLIMATE_CODE = re.compile(r"\((CLI[A-Z]{3})\)")


def climate_code(rules: str | None) -> str | None:
    """The NWS climate-report code in a market's rules ("recorded at New York City (CLINYC)")."""
    found = _CLIMATE_CODE.search(rules or "")
    return found.group(1) if found else None


class MarketIndex:
    def __init__(self, now_ns: Callable[[], int] = time.time_ns) -> None:
        self.now_ns = now_ns
        self.markets: dict[str, MarketInfo] = {}
        self.categories: dict[str, str] = {}  # series -> category
        self.fees: dict[str, FeeSchedule | None] = {}  # series -> fee terms (None: not modelled)
        self.rejects = 0
        self.combos_skipped = 0
        self.version = 0  # moves whenever a market is added, changed or removed
        # (change ``markets`` only through put and prune, so the version stays true)

    def put(self, market: Market) -> None:
        if is_combo(market.ticker):
            self.combos_skipped += 1
            return
        if _long_settled(market.status or "", market.close_time, self.now_ns()):
            if self.markets.pop(market.ticker, None) is not None:  # long settled: not kept
                self.version += 1  # a removal, so the API's sorted list is rebuilt
            return
        self.version += 1
        before = self.markets.get(market.ticker)
        code = climate_code(market.rules_primary)
        if market.rules_primary is None and before is not None:  # an update without its rules
            code = before.climate_code  # (rules that name no station clear it: merge review 6)
        event = market.event_ticker or ""
        series = series_ticker_of(event) or ""
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
            climate_code=code,
        )

    def climate_codes(self) -> dict[str, list[str]]:
        """Each NWS climate-report code named by an open market's rules, and those markets."""
        found: dict[str, list[str]] = {}
        for m in self.markets.values():
            if m.climate_code is not None and m.status in OPEN_STATUSES:
                found.setdefault(m.climate_code, []).append(m.ticker)
        return {code: sorted(tickers) for code, tickers in sorted(found.items())}

    def open_climate_codes(self) -> list[str]:
        """The climate-report codes open markets name, and nothing else (merge review 7)."""
        return sorted(
            {
                m.climate_code
                for m in self.markets.values()
                if m.climate_code is not None and m.status in OPEN_STATUSES
            }
        )

    def prune(self, now_ns: int, keep_ns: int = CLOSED_KEPT_NS) -> list[str]:
        """Forget markets that closed more than ``keep_ns`` ago (the index otherwise keeps every
        market Kalshi ever listed, M1.R 16); returns their tickers."""
        gone = [
            m.ticker
            for m in self.markets.values()
            if _long_settled(m.status, m.close_time, now_ns, keep_ns)
        ]
        for ticker in gone:
            del self.markets[ticker]
        if gone:
            self.version += 1
        return gone

    def open_markets(self) -> list[MarketInfo]:
        return [m for m in self.markets.values() if m.status in OPEN_STATUSES]

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
        index.now_ns = clock.now_ns  # the engine's clock decides what is long settled
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
