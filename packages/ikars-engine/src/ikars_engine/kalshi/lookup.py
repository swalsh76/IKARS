"""Markets the index does not have (M1.14; owner, 2026-11-01): one settled before a restart, or
longer ago than the index keeps one (``CLOSED_KEPT_NS``).

- **On request**, a screen's market (the Market screen, its chart) is looked up by ticker: Kalshi's
  live store first, then its historical one for a market settled before the cutoff. What is found
  goes into the index when the index would keep it (and so into the ``market`` channel), and into a
  bounded cache either way.
- **In the background**, titles a list needs (the recommendations) are looked up a few at a time,
  so they show on the list's next refresh.
- **For the journal**, a recommendation's market that settled while the index did not have it is
  looked up, batched by ticker, at most once every ``RETRY_NS`` per market until it settles, so
  every score lands at settlement.

Every call goes through the engine's REST client and its request budget; GET only.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from collections.abc import Callable, Iterable

from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule
from ikars_core.markets import is_combo
from ikars_core.models import Market
from ikars_core.rest import KalshiAPIError

from ikars_engine.kalshi.discovery import SETTLED_STATUSES, MarketIndex, MarketInfo

log = logging.getLogger("ikars_engine.kalshi.lookup")
NS = 1_000_000_000
KEPT = 10_000  # markets cached
OPEN_FRESH_NS = 5 * 60 * NS  # a cached market still trading is looked up again after this
MISSING_FRESH_NS = 10 * 60 * NS  # a ticker Kalshi does not know is asked again after this
RETRY_NS = 10 * 60 * NS  # an unsettled recommendation's market, at most this often
BATCH = 50  # tickers in one listing
PER_PASS = 100  # markets the journal's pass looks up at most
EVERY_S = 60.0
BACKGROUND_AT_ONCE = 20
# a ticker as Kalshi writes them: anything else is never put into a URL (merge review 8)
TICKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class MarketLookup:
    def __init__(self, data: MarketData, index: MarketIndex, now_ns: Callable[[], int]) -> None:
        self._data = data
        self._index = index
        self._now_ns = now_ns
        self._found: OrderedDict[str, tuple[int, MarketInfo | None]] = OrderedDict()
        self._tried: dict[str, int] = {}  # the journal's lookups: ticker -> when last asked
        # one lookup per ticker at a time: a second asker awaits the first's (merge review 2)
        self._flight: dict[str, asyncio.Task[MarketInfo | None]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.lookups = 0
        self.failures = 0
        self.settled = 0  # unsettled recommendations' markets found settled
        self.version = 0  # moves with every market found or not found (a page cache's key)

    # --- what is known now (never waits) -------------------------------------------------------

    def info(self, ticker: str) -> MarketInfo | None:
        """The index's details, else the cache's."""
        found = self._index.markets.get(ticker)
        if found is not None:
            return found
        cached = self._found.get(ticker)
        return None if cached is None else cached[1]

    def fees(self, ticker: str) -> FeeSchedule | None:
        info = self.info(ticker)
        return None if info is None else self._index.fees.get(info.series_ticker)

    def title(self, ticker: str) -> str:
        info = self.info(ticker)
        return "" if info is None else info.title

    def health(self) -> dict[str, object]:
        return {"cached": len(self._found), "lookups": self.lookups, "failures": self.failures,
                "settled": self.settled}  # fmt: skip

    # --- looking up -----------------------------------------------------------------------------

    async def get(self, ticker: str) -> MarketInfo | None:
        """A market's details: the index's, a fresh cached one, or looked up now (with a lookup of
        the same ticker already under way: that one's). Not a ticker's shape: None, never asked."""
        found = self._index.markets.get(ticker)
        if found is not None:
            return found
        if not TICKER.match(ticker):
            return None
        cached = self._found.get(ticker)
        if cached is not None and self._fresh(*cached):
            return cached[1]
        running = self._flight.get(ticker)
        if running is None:
            running = self._flight[ticker] = asyncio.create_task(self._one(ticker))
            running.add_done_callback(lambda _: self._flight.pop(ticker, None))
        return await asyncio.shield(running)  # one asker cancelled never cancels the others

    def want(self, tickers: Iterable[str]) -> None:
        """Look up, in the background, the markets in ``tickers`` that are not known (a list's
        titles: they show on its next refresh). A few at a time; never waits."""
        for ticker in tickers:
            if len(self._flight) >= BACKGROUND_AT_ONCE:
                return
            if self.info(ticker) is not None or ticker in self._flight or not TICKER.match(ticker):
                continue
            cached = self._found.get(ticker)
            if cached is not None and self._fresh(*cached):
                continue
            task = asyncio.create_task(self._quietly(ticker), name=f"lookup.{ticker}")
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def settle(self, unsettled: list[str]) -> int:
        """One pass for the journal: the markets of unsettled recommendations that the index does
        not have, each at most once every ``RETRY_NS``, batched; returns how many were asked."""
        now = self._now_ns()
        due = []
        for ticker in unsettled:
            if ticker in self._index.markets:
                continue  # the index settles it
            cached = self._found.get(ticker)
            if (
                cached is not None
                and cached[1] is not None
                and cached[1].status in SETTLED_STATUSES
            ):
                # settled: final, whatever the result (a voided market too: merge review 6)
                continue
            if now - self._tried.get(ticker, -RETRY_NS) < RETRY_NS or not TICKER.match(ticker):
                continue
            due.append(ticker)
            if len(due) == PER_PASS:
                break
        for start in range(0, len(due), BATCH):
            batch = due[start : start + BATCH]
            for ticker in batch:  # marked as each batch is asked: one that fails never holds back
                self._tried[ticker] = now  # the next (merge review 7)
            try:
                await self._batch(batch)
            except Exception as exc:  # this batch is asked again after RETRY_NS; the rest go on
                self.failures += 1
                log.warning("settlement lookups failed", extra={"error": type(exc).__name__})
        if len(self._tried) > KEPT:  # bounded: the oldest asks are forgotten
            for ticker in sorted(self._tried, key=self._tried.__getitem__)[
                : len(self._tried) - KEPT
            ]:
                del self._tried[ticker]
        return len(due)

    async def keep_settling(self, unsettled: Callable[[], list[str]]) -> None:
        """The journal's pass, once a minute, for as long as the engine runs."""
        while True:
            await asyncio.sleep(EVERY_S)
            try:
                await self.settle(unsettled())
            except Exception as exc:  # said in health; tried again next minute
                self.failures += 1
                log.warning("settlement lookups failed", extra={"error": type(exc).__name__})

    async def stop(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # --- the calls --------------------------------------------------------------------------------

    async def _one(self, ticker: str) -> MarketInfo | None:
        return self._keep(ticker, await self._market(ticker))

    async def _quietly(self, ticker: str) -> None:
        try:
            await self.get(ticker)
        except Exception as exc:  # a list without a title, said in health
            self.failures += 1
            log.warning(
                "market lookup failed", extra={"ticker": ticker, "error": type(exc).__name__}
            )

    async def _market(self, ticker: str) -> Market | None:
        """Kalshi's live store, then its historical one; None when neither has it."""
        self.lookups += 1
        try:
            return await self._data.market(ticker)
        except KalshiAPIError as exc:
            if exc.status != 404:
                raise
        return await self._market_historical(ticker)

    async def _batch(self, tickers: list[str]) -> None:
        """One listing for the live ones; the rest, one by one, from the historical store."""
        self.lookups += 1
        found: dict[str, Market] = {}
        async for page in self._data.markets(tickers=",".join(tickers), tolerant=True):
            found.update((m.ticker, m) for m in page.items)
        for ticker in tickers:
            market = found.get(ticker)
            if market is None:
                try:
                    market = await self._market_historical(ticker)
                except Exception as exc:  # the next pass asks again
                    self.failures += 1
                    log.warning("settlement lookup failed",
                                extra={"ticker": ticker, "error": type(exc).__name__})  # fmt: skip
                    continue
            info = self._keep(ticker, market)
            if info is not None and info.settlement() is not None:
                self.settled += 1

    async def _market_historical(self, ticker: str) -> Market | None:
        self.lookups += 1
        try:
            return await self._data.historical_market(ticker)
        except KalshiAPIError as exc:
            if exc.status != 404:
                raise
        return None

    def _keep(self, ticker: str, market: Market | None) -> MarketInfo | None:
        """Into the index when it would keep the market (recorded in the market channel), and into
        the cache either way."""
        info: MarketInfo | None = None
        if market is not None:
            if not is_combo(ticker):  # a combo is never the index's (merge review 9)
                self._index.put(market)  # kept if recent; a long settled one is not
            info = self._index.markets.get(ticker) or self._index.info_of(market)
        before = self._found.get(ticker)
        if before is None or before[1] != info:  # a page cache rebuilds only on a change
            self.version += 1  # (merge review 5)
        self._found[ticker] = (self._now_ns(), info)
        self._found.move_to_end(ticker)
        while len(self._found) > KEPT:
            self._found.popitem(last=False)
        return info

    def _fresh(self, at: int, info: MarketInfo | None) -> bool:
        age = self._now_ns() - at
        if info is None:
            return age < MISSING_FRESH_NS
        if info.status in SETTLED_STATUSES:
            return True  # settled: it no longer changes
        return age < OPEN_FRESH_NS
