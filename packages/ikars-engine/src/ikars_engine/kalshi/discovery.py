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
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule, fee_schedule
from ikars_core.fixedpoint import E6
from ikars_core.markets import is_combo, series_ticker_of
from ikars_core.models import Market

from ikars_engine.clocks import EventClock, exact_ns
from ikars_engine.store import StateStore

BOOKMARK = "discovery.updated_s"
OVERLAP_SECONDS = 120
OPEN_STATUSES = frozenset({"active", "open"})  # Kalshi's REST and WebSocket names for trading
CLOSED_KEPT_NS = 3 * 86_400 * 1_000_000_000  # a settled market stays in the index this long
SETTLED_PAGE_PAUSE_S = 0.5  # between the pages of the recently settled markets (M1.14)
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
    # what a YES contract paid once settled (Kalshi's settlement value, dollar millionths); a
    # scalar result pays a fraction, such as 50c on a tennis walkover (M1.14 live check)
    settlement_value_e6: int | None = None
    # the ticker and title in lower case, made once: the markets search reads it on every request
    # (review 1.R2 item 5)
    search: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "search", f"{self.ticker}\n{self.title}".lower())

    def settlement(self) -> tuple[str, int] | None:
        """Its result once settled and what a YES contract paid: $1 on yes, nothing on no, the
        settlement value on scalar; None before, or for a scalar result without a value (never
        guessed)."""
        if self.result == "yes":
            return "yes", E6
        if self.result == "no":
            return "no", 0
        value = self.settlement_value_e6
        if self.result == "scalar" and value is not None and 0 <= value <= E6:
            return "scalar", value
        return None


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


def market_row(
    info: MarketInfo,
    fee_terms: tuple[str, int] | None,
    at: int,
    *,
    full: bool = False,
    removed: bool = False,
) -> dict[str, Any]:
    """A market's details (and its series' fee terms) as the ``market`` channel keeps them."""
    return {
        "received_ns": at, "ticker": info.ticker, "event_ticker": info.event_ticker,
        "series_ticker": info.series_ticker, "category": info.category, "title": info.title,
        "status": info.status,
        "close_ns": None if info.close_time is None else exact_ns(info.close_time),
        "result": info.result, "settlement_value_e6": info.settlement_value_e6,
        "climate_code": info.climate_code,
        "fee_type": None if fee_terms is None else fee_terms[0],
        "fee_multiplier_e6": None if fee_terms is None else fee_terms[1],
        "full": full, "removed": removed,
    }  # fmt: skip


def _same(a: MarketInfo, b: MarketInfo) -> bool:
    """The same details, whatever the day's volume (which changes all the time and is not kept)."""
    return replace(a, volume_24h_e2=0) == replace(b, volume_24h_e2=0)


class MarketIndex:
    def __init__(self, now_ns: Callable[[], int] = time.time_ns) -> None:
        self.now_ns = now_ns
        self.markets: dict[str, MarketInfo] = {}
        self.categories: dict[str, str] = {}  # series -> category
        self.fees: dict[str, FeeSchedule | None] = {}  # series -> fee terms (None: not modelled)
        self.fee_terms: dict[
            str, tuple[str, int]
        ] = {}  # series -> (fee type, multiplier), as given
        # the market channel's writer (M1.13): a row when a market's details first appear or change,
        # and every market after a full discovery (``dump``); None: nothing recorded
        self.record: Callable[[dict[str, Any]], None] | None = None
        # during a full discovery: the markets it recorded as they changed (its dump writes the
        # rest), so a change is recorded when it comes, never held back for the cycle (sub-agent 3)
        self.cycle: set[str] | None = None
        self.rejects = 0
        self.combos_skipped = 0
        self.version = 0  # moves whenever a market is added, changed or removed
        # (change ``markets`` only through put and prune, so the version stays true)

    def put(self, market: Market, *, quietly: bool = False) -> None:
        """Keep ``market`` (or drop it, long settled). ``quietly``: the version is left for the
        caller to move once (a bulk load: M1.14)."""
        if is_combo(market.ticker):
            self.combos_skipped += 1
            return
        if _long_settled(market.status or "", market.close_time, self.now_ns()):
            gone = self.markets.pop(market.ticker, None)
            if gone is not None:  # long settled: not kept
                self.version += 1  # a removal, so the API's sorted list is rebuilt
                self._removed(gone)
            return
        if not quietly:
            self.version += 1
        before = self.markets.get(market.ticker)
        info = self.markets[market.ticker] = self.info_of(market, before)
        if self.record is not None and (before is None or not _same(before, info)):
            self.record(market_row(info, self.fee_terms.get(info.series_ticker), self.now_ns()))
            if self.cycle is not None:
                self.cycle.add(info.ticker)

    def info_of(self, market: Market, before: MarketInfo | None = None) -> MarketInfo:
        """A market's details as the index keeps them (also for one looked up and not kept:
        M1.14)."""
        code = climate_code(market.rules_primary)
        if market.rules_primary is None and before is not None:  # an update without its rules
            code = before.climate_code  # (rules that name no station clear it: merge review 6)
        event = market.event_ticker or ""
        series = series_ticker_of(event) or ""
        return MarketInfo(
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
            settlement_value_e6=market.settlement_value_e6,
        )

    def _removed(self, info: MarketInfo) -> None:
        """A market the index dropped, so a replay drops it when live did (sub-agent 4)."""
        if self.record is not None:
            terms = self.fee_terms.get(info.series_ticker)
            self.record(market_row(info, terms, self.now_ns(), removed=True))

    def dump(self, skip: set[str] | frozenset[str] = frozenset()) -> int:
        """Every market's details into the market channel (after a full discovery), except those
        in ``skip`` (recorded during the cycle), so a replay that starts at any time finds them
        all within a day; returns how many."""
        if self.record is None:
            return 0
        now = self.now_ns()
        count = 0
        for info in self.markets.values():
            if info.ticker not in skip:
                terms = self.fee_terms.get(info.series_ticker)
                self.record(market_row(info, terms, now, full=True))
                count += 1
        return count

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
            self._removed(self.markets.pop(ticker))
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
        """Series, and every unopened, open and closed (not yet settled) market; returns how many
        markets were read."""
        began = self._clock.now_ns() // 1_000_000_000
        for series in await self._data.series_list():
            if series.category:
                self.index.categories[series.ticker] = series.category
            self.index.fees[series.ticker] = fee_schedule(series.fee_type, series.fee_multiplier_e6)
            if series.fee_type is not None and series.fee_multiplier_e6 is not None:
                self.index.fee_terms[series.ticker] = (series.fee_type, series.fee_multiplier_e6)
        count = 0
        self.index.cycle = set()  # the dump below records every market not recorded meanwhile
        try:
            # the markets that trade or will, then every one closed and waiting for its result (the
            # owner may hold it: merge review 1); the settled ones come after the start
            # (``recent_settled``, M1.14)
            for status in ("unopened", "open", "closed"):
                async for page in self._data.markets(
                    status=status, mve_filter="exclude", tolerant=True
                ):
                    for market in page.items:
                        self.index.put(market)
                    count += len(page.items)
                    self.index.rejects += len(page.invalid)
        finally:
            # even when the cycle failed part-way: every market is recorded (merge review 4)
            recorded, self.index.cycle = self.index.cycle, None
            self.index.dump(recorded or frozenset())
        self._state.set_bookmark(BOOKMARK, began - OVERLAP_SECONDS)
        self.cycles += 1
        log.info("discovery: full cycle", extra={"markets": count, "rejects": self.index.rejects})
        return count

    async def recent_settled(self) -> int:
        """Markets settled in the last ``CLOSED_KEPT_NS``, so after a restart the index is what a
        running engine's is (M1.14). About 300,000 markets and two minutes of requests, so read
        after the start, in the background, never before the feed goes live (owner, 2026-11-01);
        returns how many were read."""
        since = self._clock.now_ns() // 1_000_000_000 - CLOSED_KEPT_NS // 1_000_000_000
        count = 0
        try:
            async for page in self._data.markets(
                status="settled", mve_filter="exclude", tolerant=True, min_settled_ts=since
            ):
                for market in page.items:
                    self.index.put(market, quietly=True)  # one version change at the end
                count += len(page.items)
                self.index.rejects += len(page.invalid)
                # a pause between pages leaves the request budget to the gap backfill and the
                # discovery cycles (merge review 3)
                await self._clock.sleep(SETTLED_PAGE_PAUSE_S)
        finally:
            self.index.version += 1  # the lists rebuild once, not for every market (review 4)
        log.info("discovery: recently settled markets", extra={"markets": count})
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
