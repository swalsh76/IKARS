"""Kalshi as a live source for the engine: the feed in, items and recordings out, gaps logged.

Every feed event is one of two things (from KAPS ``pump.py``):

- a **frame**: turned into an ``Item`` for the engine (with its received time), recorded in its
  channel's table, and used to keep the watched order books;
- a **loss**: written to the gap log at once, from the **last frame actually received** on that
  channel (a stalled link is noticed late, so "now" would understate it), with the way to make it
  good: trades by **backfill** from REST, lifecycle by the next **discovery** cycle, ticker and book
  data **unrecoverable** (Kalshi replays neither). A broken book or a ``seq`` gap on the book
  channel also asks Kalshi for fresh snapshots.

A **restart** without a clean shutdown is a loss too: at start, every channel's last heartbeat opens
a gap to now. Recorded: trades for every market; ticker and books for the watched set; lifecycle for
every market (owner, 2026-10-27).

Background loops run beside the feed, each catching and logging its own errors so one failure never
stops the others: discovery, the exchange, the heartbeat (which also flushes the recorder and gives
the signer the clock offset), the trade backfill, the watched set's rebalance and the storage
governor. Every REST call shares one token bucket (owner: a quarter of the account's read bucket).
"""

import asyncio
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ikars_core.auth import DataSigner
from ikars_core.book import Applied, OrderBook
from ikars_core.endpoints import MarketData
from ikars_core.models import Trade
from ikars_core.ws import Data, Reconnected, ResubscribeFailed, SequenceGap
from ikars_core.ws_models import (
    ORDERBOOK_CHANNEL,
    MarketLifecycleMsg,
    OrderbookDeltaMsg,
    OrderbookSnapshotMsg,
    TickerMsg,
    TradeMsg,
)

from ikars_engine.clocks import EventClock
from ikars_engine.governor import StorageGovernor
from ikars_engine.kalshi.discovery import Discovery
from ikars_engine.kalshi.exchange import ExchangeWatch
from ikars_engine.kalshi.watched import WatchedSet
from ikars_engine.metrics import Metrics
from ikars_engine.model import Item, Ref
from ikars_engine.recorder import Recorder
from ikars_engine.skew import SkewMonitor
from ikars_engine.sources import LiveSource
from ikars_engine.store import StateStore

log = logging.getLogger("ikars_engine.kalshi")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FULL_BOOKMARK = "discovery.full_ns"
MINUTE_NS = 60 * 1_000_000_000
MILLISECOND = timedelta(milliseconds=1)
NS = 1_000_000_000
CHANNEL_OF_TYPE = {
    "ticker": "ticker",
    "trade": "trade",
    "market_lifecycle_v2": "lifecycle",
    "event_lifecycle": "lifecycle",
    "orderbook_snapshot": "book",
    "orderbook_delta": "book",
}
RECOVERY = {
    "trade": "backfill",
    "lifecycle": "discovery",
    "ticker": "unrecoverable",
    "book": "unrecoverable",
}
SUBSCRIPTION_CHANNELS = {
    "ticker": "ticker",
    "trade": "trade",
    "market_lifecycle_v2": "lifecycle",
    ORDERBOOK_CHANNEL: "book",
}


class Feed(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int: ...
    async def update_subscription(
        self, sid: int, action: str, market_tickers: list[str]
    ) -> list[str]: ...
    async def get_snapshot(self, sid: int, market_tickers: list[str]) -> None: ...
    def events(self) -> AsyncIterator[Any]: ...
    def channel_of(self, sid: int) -> str | None: ...
    def stats(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class Intervals:
    discovery_s: float = 60.0
    full_discovery_s: float = 86_400.0
    exchange_s: float = 60.0
    heartbeat_s: float = 5.0
    backfill_s: float = 10.0
    rebalance_s: float = 300.0
    governor_s: float = 600.0
    backfill_pad_s: int = 5
    backfill_slice_s: int = 3_600
    backfill_attempts: int = 5


DEFAULT_INTERVALS = Intervals()


class KalshiSource:
    def __init__(
        self,
        *,
        feed: Feed,
        data: MarketData,
        discovery: Discovery,
        exchange: ExchangeWatch,
        state: StateStore,
        recorder: Recorder,
        clock: EventClock,
        metrics: Metrics,
        skew: SkewMonitor,
        watched: WatchedSet,
        governor: StorageGovernor | None = None,
        signer: DataSigner | None = None,
        intervals: Intervals = DEFAULT_INTERVALS,
    ) -> None:
        self._feed = feed
        self._data = data
        self.discovery = discovery
        self.exchange = exchange
        self._state = state
        self.recorder = recorder
        self._clock = clock
        self._metrics = metrics
        self._skew = skew
        self.watched = watched
        self._governor = governor
        self._signer = signer
        self._every = intervals
        self._out = LiveSource()
        self._tasks: list[asyncio.Task[None]] = []
        self.books: dict[str, OrderBook] = {}
        self.last_received: dict[str, int] = {}
        self.last_price_e6: dict[str, int] = {}
        self._book_sid: int | None = None
        self._snapshots = 0
        self._attempts: Counter[int] = Counter()
        self.counts: Counter[str] = Counter()
        self.storage: dict[str, object] = {}

    # --- life cycle --------------------------------------------------------------------------

    async def start(self) -> None:
        now = self._clock.now_ns()
        recovered = self.recorder.recover()
        if recovered:
            log.info(
                "recorder: compacted hours a previous run left open", extra={"files": recovered}
            )
        previous = (self._state.heartbeats(), self._state.shut_down_cleanly())
        await self.discovery.full()
        self._state.set_bookmark(FULL_BOOKMARK, now)  # the next cycle is an incremental one
        await self.exchange.poll()
        await self.exchange.refresh_schedule()
        await self._feed.start()
        for channel in ("ticker", "trade", "market_lifecycle_v2"):
            await self._feed.subscribe(channel)  # every market
        self.watched.current = self.watched.choose(self.discovery.index, self.last_price_e6, now)
        self.watched.measure(now)
        if self.watched.current:
            self._book_sid = await self._feed.subscribe(
                ORDERBOOK_CHANNEL, sorted(self.watched.current)
            )
        # a run that died: every channel is missing from its last heartbeat until now, when the
        # subscriptions are live again (not when start() began: discovery takes minutes)
        self._gaps_from_restart(*previous)
        loops: list[tuple[str, Callable[[], Awaitable[None]], float]] = [
            ("discovery", self._discover, self._every.discovery_s),
            ("exchange", self._poll_exchange, self._every.exchange_s),
            ("heartbeat", self._heartbeat, self._every.heartbeat_s),
            ("backfill", self._backfill_open_gaps, self._every.backfill_s),
            ("rebalance", self._rebalance, self._every.rebalance_s),
            ("governor", self._govern, self._every.governor_s),
        ]
        self._tasks = [asyncio.create_task(self._pump(), name="kalshi.pump")]
        self._tasks += [
            asyncio.create_task(self._every_so_often(name, step, seconds), name=f"kalshi.{name}")
            for name, step, seconds in loops
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._feed.close()
        self._heartbeat_now()
        self.recorder.close()
        self._state.mark_clean_shutdown(self._clock.now_ns())
        await self._out.close()

    def items(self) -> AsyncIterator[Item]:
        return self._out.items()

    async def _every_so_often(
        self, name: str, step: Callable[[], Awaitable[None]], seconds: float
    ) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await step()
            except Exception as exc:  # one loop's failure never stops the others
                self._metrics.count(f"kalshi.{name}.errors")
                log.warning("background step failed", extra={"step": name, "error": repr(exc)})

    # --- the feed ----------------------------------------------------------------------------

    async def _pump(self) -> None:
        async for event in self._feed.events():
            if isinstance(event, Data):
                await self._frame(event)
            elif isinstance(event, Reconnected):
                self._lost_link(event)
            elif isinstance(event, SequenceGap):
                await self._sequence_gap(event)
            elif isinstance(event, ResubscribeFailed):
                channel = SUBSCRIPTION_CHANNELS.get(event.channel, event.channel)
                self._gap(
                    channel,
                    "resubscribe failed",
                    self.last_received.get(channel, self._clock.now_ns()),
                    event.error,
                )

    async def _frame(self, event: Data) -> None:
        frame = event.frame
        message = frame.payload
        at = event.received_ns
        channel = CHANNEL_OF_TYPE.get(frame.type)
        if channel is None or message is None:
            return
        self.last_received[channel] = at
        self.counts[frame.type] += 1
        if frame.sending_ts_ms:
            self._skew.add_frame(at // 1_000_000, frame.sending_ts_ms)
        published: int | None = None
        if isinstance(message, TickerMsg):
            ticker = message.market_ticker
            if message.price_e6 is not None:
                self.last_price_e6[ticker] = message.price_e6
            published = message.ts_ms
            if ticker in self.watched.current:
                self.recorder.write("ticker", self._ticker_row(message, at))
        elif isinstance(message, TradeMsg):
            ticker = message.market_ticker
            published = message.ts_ms
            self.recorder.write("trade", _trade_row(message, at))
        elif isinstance(message, MarketLifecycleMsg):
            ticker = message.market_ticker or ""
            self.recorder.write(
                "lifecycle",
                {"received_ns": at, "ticker": ticker, "event_type": message.event_type,
                 "result": message.result,
                 "message": json.dumps(frame.raw.get("msg"), default=str)},
            )  # fmt: skip
        elif isinstance(message, OrderbookSnapshotMsg):
            ticker = message.market_ticker
            book = self.books.setdefault(ticker, OrderBook(ticker))
            book.apply_snapshot(message)
            self._snapshots += 1
            for side, levels in (("yes", message.yes), ("no", message.no)):
                for price, count in levels:
                    self.recorder.write(
                        "book_snapshot",
                        {"received_ns": at, "ticker": ticker, "snapshot": self._snapshots,
                         "side": side, "price_e6": price, "count_e2": count},
                    )  # fmt: skip
        elif isinstance(message, OrderbookDeltaMsg):
            ticker = message.market_ticker
            published = message.ts_ms
            self.watched.note_delta(ticker)
            book = self.books.setdefault(ticker, OrderBook(ticker))
            if book.apply_delta(message) is Applied.BROKEN:
                self._metrics.count("kalshi.book.broken")
                await self._snapshot([ticker])
            self.recorder.write(
                "book_delta",
                {"received_ns": at, "ts_ms": message.ts_ms, "ticker": ticker, "seq": frame.seq,
                 "side": message.side, "price_e6": message.price_e6, "delta_e2": message.delta_e2},
            )  # fmt: skip
        else:
            return
        await self._out.put(
            Item(
                ref=Ref(frame.type, ticker),
                source="kalshi",
                received_ns=at,
                published_ns=None if published is None else published * 1_000_000,
                payload=message.model_dump(mode="json"),
            )
        )

    @staticmethod
    def _ticker_row(message: TickerMsg, at: int) -> dict[str, Any]:
        return {
            "received_ns": at, "ts_ms": message.ts_ms, "ticker": message.market_ticker,
            "price_e6": message.price_e6, "yes_bid_e6": message.yes_bid_e6,
            "yes_ask_e6": message.yes_ask_e6, "yes_bid_size_e2": message.yes_bid_size_e2,
            "yes_ask_size_e2": message.yes_ask_size_e2, "volume_e2": message.volume_e2,
            "open_interest_e2": message.open_interest_e2,
        }  # fmt: skip

    async def _snapshot(self, tickers: list[str]) -> None:
        if self._book_sid is None or not tickers:
            return
        try:
            await self._feed.get_snapshot(self._book_sid, tickers)
            self._metrics.count("kalshi.book.snapshot_requests")
        except Exception as exc:
            log.warning("snapshot request failed", extra={"error": repr(exc)})

    # --- losses ------------------------------------------------------------------------------

    def _gap(self, channel: str, reason: str, start_ns: int, detail: str = "") -> int:
        now = self._clock.now_ns()
        gap_id = self._state.add_gap(
            channel,
            reason,
            min(start_ns, now),
            now,
            RECOVERY.get(channel, "unrecoverable"),
            now,
            detail,
        )
        self._metrics.count(f"kalshi.gaps.{channel}")
        log.warning("feed gap", extra={"channel": channel, "reason": reason, "gap": gap_id})
        return gap_id

    def _lost_link(self, event: Reconnected) -> None:
        now = self._clock.now_ns()
        for channel in ("trade", "ticker", "lifecycle", "book"):
            self._gap(
                channel,
                f"reconnect ({event.reason})",
                self.last_received.get(channel, now),
                f"down {event.downtime:.1f} s",
            )
        self.books.clear()  # every book is rebuilt from the snapshots the resubscription brings

    async def _sequence_gap(self, event: SequenceGap) -> None:
        name = self._feed.channel_of(event.sid) or ""
        channel = SUBSCRIPTION_CHANNELS.get(name, name)
        self._gap(
            channel,
            "sequence",
            self.last_received.get(channel, self._clock.now_ns()),
            f"expected {event.expected}, got {event.got}",
        )
        if channel == "book":
            await self._snapshot(sorted(self.watched.current))

    def _gaps_from_restart(self, beats: dict[str, int], clean: bool) -> None:
        if beats and not clean:
            for channel, at in sorted(beats.items()):
                self._gap(channel, "restart", at)

    # --- background steps --------------------------------------------------------------------

    async def _discover(self) -> None:
        began = self._clock.now_ns()
        since_full = began - (self._state.bookmark(FULL_BOOKMARK) or 0)
        if since_full >= self._every.full_discovery_s * NS:
            await self.discovery.full()
            self._state.set_bookmark(FULL_BOOKMARK, self._clock.now_ns())
        else:
            await self.discovery.incremental()
        for gap in self._state.gaps("open"):
            if gap.recovery == "discovery" and gap.end_ns <= began:  # this cycle covered it
                self._state.finish_gap(gap.id, "done", "a discovery cycle ran after it")

    async def _poll_exchange(self) -> None:
        await self.exchange.poll()

    def _heartbeat_now(self) -> None:
        if self.last_received:
            self._state.beat(dict(self.last_received))

    async def _heartbeat(self) -> None:
        self._heartbeat_now()
        self.recorder.flush()
        if self._signer is not None:
            self._signer.offset_ms = self._skew.offset_ms

    async def _backfill_open_gaps(self) -> None:
        for gap in self._state.gaps("open"):
            if gap.recovery != "backfill":
                continue
            try:
                count = await self._backfill(gap.start_ns, gap.end_ns)
            except Exception as exc:
                self._attempts[gap.id] += 1
                if self._attempts[gap.id] >= self._every.backfill_attempts:
                    self._state.finish_gap(
                        gap.id, "failed", f"{self._attempts[gap.id]} attempts: {exc!r}"[:300]
                    )
                continue
            self._state.finish_gap(gap.id, "done", f"{count} trades fetched")

    async def _backfill(self, start_ns: int, end_ns: int) -> int:
        """Every trade in the window (padded), in slices; safe to repeat: the recorder keeps one row
        per ``trade_id``. ``min_ts`` is inclusive and ``max_ts`` exclusive, in whole seconds."""
        first = start_ns // NS - self._every.backfill_pad_s
        last = -(-end_ns // NS) + self._every.backfill_pad_s
        cutoff = int((await self._data.historical_cutoff()).trades_created_ts.timestamp())
        known = self.recorder.known_trade_ids(start_ns - MINUTE_NS, end_ns + MINUTE_NS)
        count = 0
        for low in range(first, last, self._every.backfill_slice_s):
            high = min(low + self._every.backfill_slice_s, last)
            # a slice that crosses the cutoff is read from both stores, split at the cutoff
            for historical, a, b in (
                (True, low, min(high, cutoff)),
                (False, max(low, cutoff), high),
            ):
                if a >= b:
                    continue
                reader = self._data.historical_trades if historical else self._data.trades
                async for page in reader(min_ts=a, max_ts=b):
                    for trade in page.items:
                        if trade.trade_id in known:  # the feed had it: never stored twice
                            continue
                        known.add(trade.trade_id)
                        self.recorder.write("trade", _rest_trade_row(trade, self._clock.now_ns()))
                        count += 1
        self._metrics.count("kalshi.backfill.trades", count)
        return count

    async def _rebalance(self) -> None:
        now = self._clock.now_ns()
        self.watched.measure(now)
        wanted = self.watched.choose(self.discovery.index, self.last_price_e6, now)
        added, removed = (
            sorted(wanted - self.watched.current),
            sorted(self.watched.current - wanted),
        )
        if self._book_sid is None and wanted:
            self._book_sid = await self._feed.subscribe(ORDERBOOK_CHANNEL, sorted(wanted))
        elif self._book_sid is not None:
            if added:
                await self._feed.update_subscription(self._book_sid, "add_markets", added)
            if removed:
                await self._feed.update_subscription(self._book_sid, "delete_markets", removed)
                for ticker in removed:
                    self.books.pop(ticker, None)
        self.watched.current = wanted

    async def _govern(self) -> None:
        if self._governor is None:
            return
        report = await asyncio.to_thread(self._governor.check)
        self.storage = {
            "state": str(report.state),
            "used_bytes": report.used_bytes,
            "budget_bytes": report.budget_bytes,
            "pruned": len(report.pruned),
        }
        if report.pruned:
            log.warning("storage: oldest recordings pruned", extra={"files": len(report.pruned)})

    def health(self) -> dict[str, object]:
        return {
            "frames": dict(self.counts),
            "watched": {
                "markets": len(self.watched.current),
                "deltas_per_s": round(self.watched.live_rate(self._clock.now_ns())),
                "held_out": len(self.watched.held_out),
            },
            "books_ready": sum(1 for b in self.books.values() if b.ready),
            "open_gaps": len(self._state.gaps("open")),
            "markets_known": len(self.discovery.index.markets),
            "trading_active": self.exchange.trading,
            "recorded_rows": dict(self.recorder.rows_written),
            "storage": self.storage,
            "feed": {k: v for k, v in self._feed.stats().items() if not isinstance(v, dict)},
        }


def _trade_row(message: TradeMsg, at: int) -> dict[str, Any]:
    return {
        "received_ns": at, "ts_ms": message.ts_ms, "ticker": message.market_ticker,
        "trade_id": message.trade_id, "yes_price_e6": message.yes_price_e6,
        "no_price_e6": message.no_price_e6, "count_e2": message.count_e2,
        "taker_side": message.taker_side, "backfilled": False,
    }  # fmt: skip


def _rest_trade_row(trade: Trade, at: int) -> dict[str, Any]:
    return {
        "received_ns": at, "ts_ms": (trade.created_time - EPOCH) // MILLISECOND,
        "ticker": trade.ticker, "trade_id": trade.trade_id, "yes_price_e6": trade.yes_price_e6,
        "no_price_e6": trade.no_price_e6, "count_e2": trade.count_e2,
        "taker_side": trade.taker_side, "backfilled": True,
    }  # fmt: skip
