"""Discovery, the exchange watch, the watched set and the Kalshi source, against a fake REST service
(the core package's made-up fixtures) and a fake feed carrying real-shaped frames."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import KalshiPublicClient
from ikars_core.ws import Data, FeedError, Reconnected, ResubscribeFailed, SequenceGap
from ikars_core.ws_models import parse_frame
from ikars_engine.clocks import VirtualClock
from ikars_engine.kalshi.discovery import BOOKMARK, Discovery, MarketIndex, series_of
from ikars_engine.kalshi.exchange import ExchangeWatch
from ikars_engine.kalshi.runner import _at, clock_observer, summary
from ikars_engine.kalshi.source import Intervals, KalshiSource
from ikars_engine.kalshi.watched import WatchedSet
from ikars_engine.metrics import Metrics
from ikars_engine.recorder import Recorder, hours, read_rows
from ikars_engine.skew import SkewMonitor
from ikars_engine.store import StateStore

FIXTURES = Path(__file__).parents[2] / "ikars-core" / "tests" / "fixtures"
T0 = 1_767_225_600 * 10**9  # 2026-01-01 00:00 UTC


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def market(
    ticker: str, *, status: str = "active", volume: str = "100.00", event: str = "KXDEMO-26JAN01"
) -> dict[str, Any]:
    return {
        **fixture("market_open.json"),
        "ticker": ticker,
        "status": status,
        "volume_24h_fp": volume,
        "event_ticker": event,
    }


class FakeKalshi:
    """The REST endpoints the source uses, with state the tests can change."""

    def __init__(self) -> None:
        self.markets: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []  # returned for min_updated_ts queries
        self.status = fixture("exchange_status.json")
        self.trades: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.fail = False  # every request answers 404
        self.on_markets: Any = None  # called on each market listing (to let time pass)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(404)
        path = request.url.path.removeprefix("/trade-api/v2")
        params = request.url.params
        body: dict[str, Any]
        if path == "/series":
            body = {"series": [fixture("series.json")]}
        elif path == "/markets" and "min_updated_ts" in params:
            body = {"markets": self.updated, "cursor": ""}
        elif path == "/markets":
            if self.on_markets is not None:
                self.on_markets()
            body = {
                "markets": [
                    m
                    for m in self.markets
                    if params.get("status") == "open" or m["status"] == "unopened"
                ],
                "cursor": "",
            }
            if params.get("status") == "unopened":
                body["markets"] = [m for m in self.markets if m["status"] == "unopened"]
            else:
                body["markets"] = [m for m in self.markets if m["status"] != "unopened"]
        elif path == "/exchange/status":
            body = self.status
        elif path == "/exchange/schedule":
            body = {"schedule": fixture("exchange_schedule.json")}
        elif path == "/historical/cutoff":
            body = fixture("cutoff.json")
        elif path in ("/markets/trades", "/historical/trades"):
            body = {"trades": self.trades, "cursor": ""}
        else:
            return httpx.Response(404)
        return httpx.Response(200, content=json.dumps(body).encode())

    def data(self) -> MarketData:
        client = KalshiPublicClient(
            KalshiConfig(environment=Environment.PRODUCTION),
            bucket=TokenBucket(rate=1e9, capacity=1e9),
            transport=httpx.MockTransport(self.handle),
        )
        return MarketData(client)


class FakeFeed:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self.subscriptions: dict[int, tuple[str, list[str] | None]] = {}
        self.updates: list[tuple[int, str, list[str]]] = []
        self.snapshots: list[list[str]] = []
        self.snapshot_sids: list[int] = []
        self.resubscribed: list[str] = []
        self.refuse_resubscribe = 0  # how many resubscribes fail before one holds
        self.cap: int | None = None  # the exchange refuses adds past this many markets
        self.closed = False

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)

    async def subscribe(self, channel: str, market_tickers: list[str] | None = None) -> int:
        sid = len(self.subscriptions) + 1
        self.subscriptions[sid] = (channel, market_tickers)
        return sid

    async def update_subscription(
        self, sid: int, action: str, market_tickers: list[str]
    ) -> list[str]:
        self.updates.append((sid, action, market_tickers))
        channel, held = self.subscriptions[sid]
        now = set(held or [])
        now = now - set(market_tickers) if action == "delete_markets" else now | set(market_tickers)
        if self.cap is not None and len(now) > self.cap:
            raise FeedError("too many markets")
        self.subscriptions[sid] = (channel, sorted(now))
        return sorted(now)

    async def resubscribe(self, channel: str) -> int:
        self.resubscribed.append(channel)
        if self.refuse_resubscribe:
            self.refuse_resubscribe -= 1
            raise FeedError("refused")
        return self.sid_of(channel) or 0

    async def get_snapshot(self, sid: int, market_tickers: list[str]) -> None:
        self.snapshots.append(market_tickers)
        self.snapshot_sids.append(sid)

    async def events(self) -> AsyncIterator[Any]:
        while (event := await self.queue.get()) is not None:
            yield event

    def channel_of(self, sid: int) -> str | None:
        entry = self.subscriptions.get(sid)
        return None if entry is None else entry[0]

    def stats(self) -> dict[str, Any]:
        return {"connected": True, "sequence_gaps": 0}

    def book_sid(self) -> int:
        sid = self.sid_of("orderbook_delta")
        assert sid is not None
        return sid

    def sid_of(self, channel: str) -> int | None:
        return next((sid for sid, (name, _) in self.subscriptions.items() if name == channel), None)


def frame(kind: str, msg: dict[str, Any], at: int, seq: int | None = None, sid: int = 1) -> Data:
    raw = {"type": kind, "sid": sid, "msg": msg, "sending_ts_ms": at // 10**6 - 40}
    if seq is not None:
        raw["seq"] = seq
    return Data(parse_frame(json.dumps(raw)), at, at, 1)


TICKER_MSG = {
    "market_ticker": "M1",
    "price_dollars": "0.5000",
    "yes_bid_dollars": "0.4900",
    "yes_ask_dollars": "0.5100",
    "ts_ms": 1,
}
TRADE_MSG = {
    "trade_id": "t1",
    "market_ticker": "M1",
    "yes_price_dollars": "0.5000",
    "no_price_dollars": "0.5000",
    "count_fp": "3.00",
    "taker_side": "yes",
    "ts_ms": 2,
}
SNAPSHOT_MSG = {
    "market_ticker": "M1",
    "yes_dollars_fp": [["0.4900", "10.00"]],
    "no_dollars_fp": [["0.4900", "5.00"]],
}
DELTA_MSG = {
    "market_ticker": "M1",
    "price_dollars": "0.4900",
    "delta_fp": "-1.00",
    "side": "yes",
    "ts_ms": 3,
}


RIGS: list["Rig"] = []


@pytest.fixture(autouse=True)
def close_rigs() -> Iterator[None]:
    yield
    while RIGS:
        RIGS.pop().state.close()


class Rig:
    def __init__(self, tmp_path: Path, markets: list[dict[str, Any]]) -> None:
        RIGS.append(self)
        self.kalshi = FakeKalshi()
        self.kalshi.markets = markets
        self.clock = VirtualClock(T0)
        self.state = StateStore(tmp_path / "state.sqlite")
        self.recorder = Recorder(tmp_path / "rec", flush_seconds=3600)
        self.feed = FakeFeed()
        self.metrics = Metrics()
        data = self.kalshi.data()
        self.discovery = Discovery(data, MarketIndex(), self.state, self.clock)
        self.source = KalshiSource(
            feed=self.feed,
            data=data,
            discovery=self.discovery,
            exchange=ExchangeWatch(data, self.state, self.clock),
            state=self.state,
            recorder=self.recorder,
            clock=self.clock,
            metrics=self.metrics,
            skew=SkewMonitor(),
            watched=WatchedSet(max_markets=2),
            intervals=Intervals(
                discovery_s=3600,
                exchange_s=3600,
                heartbeat_s=3600,
                backfill_s=3600,
                rebalance_s=3600,
                governor_s=3600,
                resubscribe_s=0.001,
            ),
        )

    async def items(self, count: int) -> list[Any]:
        out = []
        async for item in self.source.items():
            out.append(item)
            if len(out) == count:
                break
        return out


# --- discovery and the exchange -----------------------------------------------------------------


async def test_full_discovery_indexes_markets_with_categories_and_skips_combos(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [market("A"), market("KXMVE-COMBO"), market("U", status="unopened")])
    assert await rig.discovery.full() == 3
    index = rig.discovery.index
    assert set(index.markets) == {"A", "U"} and index.combos_skipped == 1
    assert index.markets["A"].category == "Crypto" and index.markets["A"].series_ticker == "KXDEMO"
    assert rig.state.bookmark(BOOKMARK) == T0 // 10**9 - 120
    assert [m.ticker for m in index.open_markets()] == ["A"]
    assert index.snapshot(tmp_path / "markets.parquet") == 2


async def test_incremental_discovery_uses_the_bookmark_and_runs_full_without_one(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [market("A")])
    assert await rig.discovery.incremental() == 1  # no bookmark yet: a full cycle
    rig.kalshi.updated = [market("A", status="closed")]
    rig.clock.advance_to(T0 + 60 * 10**9)
    assert await rig.discovery.incremental() == 1
    query = rig.kalshi.requests[-1].url.params
    assert query["min_updated_ts"] == str(T0 // 10**9 - 120) and "status" not in query
    assert rig.discovery.index.markets["A"].status == "closed"
    assert series_of("KXHIGHNY-26OCT28") == "KXHIGHNY"


async def test_the_exchange_status_is_stored_only_when_it_changes(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [])
    watch = rig.source.exchange
    assert watch.trading is None
    assert await watch.poll() is True and await watch.poll() is False
    rig.kalshi.status = {**rig.kalshi.status, "trading_active": False}
    assert await watch.poll() is True and watch.trading is False and watch.changes == 2
    await watch.refresh_schedule()
    assert watch.schedule is not None


# --- the watched set ----------------------------------------------------------------------------


def index_of(*markets: tuple[str, int]) -> MarketIndex:
    from ikars_core.models import Market

    index = MarketIndex()
    for ticker, volume in markets:
        index.put(Market.model_validate(market(ticker, volume=f"{volume}.00")))
    return index


def test_pinned_first_then_by_volume_inside_the_price_band() -> None:
    watched = WatchedSet(max_markets=3, pinned={"P"})
    index = index_of(("A", 10), ("B", 30), ("C", 20), ("CHEAP", 99))
    chosen = watched.choose(index, {"CHEAP": 50_000, "B": 500_000}, T0)
    assert chosen == {"P", "B", "C"}  # CHEAP is outside 20-80 cents; A has the least volume


def test_over_the_delta_budget_the_hottest_unpinned_markets_are_held_out() -> None:
    watched = WatchedSet(max_markets=3, max_deltas_per_second=100, pinned={"P"})
    watched.current = {"P", "HOT", "B"}
    watched.measure(T0)
    for _ in range(900):
        watched.note_delta("HOT")
    for _ in range(50):
        watched.note_delta("B")
    for _ in range(80):
        watched.note_delta("P")
    assert watched.live_rate(T0 + 10 * 10**9) == pytest.approx(103.0)
    watched.measure(T0 + 10 * 10**9)  # HOT 90/s, B 5/s, P 8/s
    chosen = watched.choose(index_of(("HOT", 99), ("B", 50)), {}, T0 + 10 * 10**9)
    assert chosen == {"P", "B"} and "HOT" in watched.held_out
    assert watched.total_rate() == pytest.approx(103.0)
    later = T0 + 2 * 3_600 * 10**9  # the hold-out ends after an hour
    assert "HOT" in watched.choose(index_of(("HOT", 99)), {}, later) or watched.held_out == {}


# --- the source ---------------------------------------------------------------------------------


async def test_frames_become_items_and_recordings_and_keep_the_books(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1"), market("M2")])
    await rig.source.start()
    book_sid = rig.feed.book_sid()
    assert set(rig.feed.subscriptions[book_sid][1] or []) == {"M1", "M2"}
    for event in (
        frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + 1, seq=1, sid=book_sid),
        frame("orderbook_delta", DELTA_MSG, T0 + 2, seq=2, sid=book_sid),
        frame("ticker", TICKER_MSG, T0 + 3, sid=1),
        frame("trade", TRADE_MSG, T0 + 4, seq=1, sid=2),
    ):
        await rig.feed.queue.put(event)
    items = await rig.items(4)
    assert [i.ref.kind for i in items] == [
        "orderbook_snapshot",
        "orderbook_delta",
        "ticker",
        "trade",
    ]
    assert items[3].received_ns == T0 + 4 and items[3].published_ns == 2 * 10**6
    assert rig.source.books["M1"].best_bid("yes") == (490_000, 900)
    assert rig.source.last_price_e6["M1"] == 500_000
    await rig.source.stop()
    trades = read_rows(hours(tmp_path / "rec", "trade"), "trade")
    assert trades.column("trade_id").to_pylist() == ["t1"]
    assert read_rows(hours(tmp_path / "rec", "ticker"), "ticker").num_rows == 1  # M1 is watched
    assert rig.state.shut_down_cleanly() and rig.feed.closed


async def test_a_reconnect_opens_a_gap_per_channel_from_its_last_frame(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    await rig.feed.queue.put(frame("trade", TRADE_MSG, T0 + 5, seq=1, sid=2))
    await rig.items(1)
    rig.clock.advance_to(T0 + 10 * 10**9)
    await rig.feed.queue.put(Reconnected(2, "connection_lost", 3.0))
    await rig.feed.queue.put(
        frame("trade", {**TRADE_MSG, "trade_id": "t2"}, T0 + 11 * 10**9, seq=1, sid=2)
    )
    await rig.items(1)
    gaps = {g.channel: g for g in rig.state.gaps()}
    assert gaps["trade"].start_ns == T0 + 5 and gaps["trade"].recovery == "backfill"
    assert gaps["ticker"].status == "unrecoverable" and gaps["book"].status == "unrecoverable"
    assert gaps["lifecycle"].recovery == "discovery"
    await rig.source.stop()


async def test_a_book_sequence_gap_asks_for_snapshots_and_a_broken_book_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    sid = rig.feed.book_sid()
    await rig.feed.queue.put(SequenceGap(sid, 5, 9, 1))
    await rig.feed.queue.put(frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + 1, seq=9, sid=sid))
    await rig.feed.queue.put(
        frame("orderbook_delta", {**DELTA_MSG, "delta_fp": "-99.00"}, T0 + 2, seq=10, sid=sid)
    )
    await rig.items(2)
    await asyncio.sleep(0)  # the requests run beside the pump
    assert rig.feed.snapshots == [["M1"], ["M1"]]  # once for the gap, once for the broken book
    assert rig.state.gaps()[0].channel == "book" and rig.metrics.counters["kalshi.book.broken"] == 1
    await rig.feed.queue.put(ResubscribeFailed("trade", "refused", 2))
    await rig.feed.queue.put(frame("ticker", TICKER_MSG, T0 + 3, sid=1))
    await rig.items(1)
    assert rig.state.gaps()[-1].reason == "resubscribe failed"
    await rig.source.stop()


async def test_a_restart_without_a_clean_shutdown_opens_gaps_from_the_heartbeats(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [market("M1")])
    rig.state.beat({"trade": T0 - 30 * 10**9, "ticker": T0 - 31 * 10**9})  # a run that died
    await rig.source.start()
    gaps = {g.channel: g for g in rig.state.gaps()}
    assert gaps["trade"].reason == "restart" and gaps["trade"].start_ns == T0 - 30 * 10**9
    await rig.source.stop()


LATE = 1_790_000_000 * 10**9  # 2026-09-21, after the fixture's historical cutoff (2026-08-22)


async def test_the_backfill_fetches_the_window_and_closes_the_gap(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    gap = rig.state.add_gap("trade", "reconnect", LATE - 10 * 10**9, LATE, "backfill", LATE)
    rig.kalshi.trades = [fixture("trade.json")]
    await rig.source._backfill_open_gaps()
    done = rig.state.gaps("done")[0]
    assert done.id == gap and done.detail == "1 trades fetched"
    query = rig.kalshi.requests[-1].url
    assert query.path.endswith("/markets/trades")  # after the cutoff: the live endpoint
    assert query.params["min_ts"] == str(LATE // 10**9 - 15)
    assert query.params["max_ts"] == str(LATE // 10**9 + 5)  # exclusive, padded
    await rig.source.stop()
    rows = read_rows(hours(tmp_path / "rec", "trade"), "trade")
    assert rows.column("backfilled").to_pylist() == [True]


async def test_a_window_before_the_cutoff_reads_the_historical_trades(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    rig.state.add_gap("trade", "restart", T0 - 10 * 10**9, T0, "backfill", T0)  # January 2026
    await rig.source._backfill_open_gaps()
    assert rig.kalshi.requests[-1].url.path.endswith("/historical/trades")
    await rig.source.stop()


async def test_the_first_cycle_after_start_is_incremental(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("A")])
    await rig.source.start()
    before = len(rig.kalshi.requests)
    rig.clock.advance_to(T0 + 60 * 10**9)
    await rig.source._discover()
    new = rig.kalshi.requests[before:]
    assert len(new) == 1 and "min_updated_ts" in new[0].url.params  # not a second full listing
    await rig.source.stop()


async def test_a_backfill_that_keeps_failing_is_marked_failed(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    rig.state.add_gap("trade", "reconnect", T0 - 10 * 10**9, T0, "backfill", T0)
    rig.kalshi.fail = True
    for _ in range(5):
        await rig.source._backfill_open_gaps()
    assert rig.state.gaps("failed")[0].detail.startswith("5 attempts")
    await rig.source.stop()


async def test_the_rebalance_adds_and_removes_markets_and_discovery_closes_lifecycle_gaps(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [market("A", volume="10.00"), market("B", volume="5.00")])
    await rig.source.start()
    assert rig.source.watched.current == {"A", "B"}
    rig.kalshi.updated = [market("C", volume="99.00"), market("B", status="closed")]
    rig.state.add_gap("lifecycle", "reconnect", T0, T0, "discovery", T0)
    await rig.source._discover()
    assert rig.state.gaps("open") == []
    await rig.source._rebalance()
    sid = rig.feed.sid_of("orderbook_delta")
    assert (sid, "add_markets", ["C"]) in rig.feed.updates and (
        sid,
        "delete_markets",
        ["B"],
    ) in rig.feed.updates
    health = rig.source.health()
    assert health["watched"] == {"markets": 2, "deltas_per_s": 0, "held_out": 0}
    await rig.source.stop()


# --- the runner's helpers -----------------------------------------------------------------------


def test_the_clock_observer_samples_at_most_once_a_minute() -> None:
    skew = SkewMonitor()
    observe = clock_observer(skew)
    observe(1000.0, 1000.1, {"date": "Thu, 01 Jan 1970 00:16:40 GMT"})
    observe(1010.0, 1010.1, {"date": "Thu, 01 Jan 1970 00:16:50 GMT"})  # too soon
    observe(1070.0, 1070.1, {})  # no Date header: ignored
    assert skew.report()["samples"] == 1


def test_the_summary_line_reads_nested_health() -> None:
    health: dict[str, object] = {
        "kalshi": {"frames": {"trade": 3}, "open_gaps": 0},
        "metrics": {"timings": {"engine.feed_to_route_ns": {"p99_ns": 50_000_000}}},
        "clock": {"status": "ok"},
    }
    line = json.loads(summary(health))
    assert line["frames"] == {"trade": 3} and line["feed_to_route_p99_ms"] == 50.0
    assert _at(health, "kalshi", "missing", "x") is None


CUTOFF_S = 1_787_356_800  # the fixture's trades cutoff, 2026-08-22 00:00 UTC


async def test_a_slice_across_the_cutoff_reads_both_stores(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    rig.state.add_gap(
        "trade", "restart", (CUTOFF_S - 600) * 10**9, (CUTOFF_S + 600) * 10**9, "backfill", T0
    )
    before = len(rig.kalshi.requests)
    await rig.source._backfill_open_gaps()
    reads = [
        (r.url.path.rsplit("/", 2)[-2], r.url.params["min_ts"], r.url.params["max_ts"])
        for r in rig.kalshi.requests[before + 1 :]
    ]
    assert reads == [
        ("historical", str(CUTOFF_S - 605), str(CUTOFF_S)),
        ("markets", str(CUTOFF_S), str(CUTOFF_S + 605)),
    ]
    await rig.source.stop()


async def test_a_backfill_stores_only_what_the_feed_missed(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    rig.recorder = Recorder(tmp_path / "rec", flush_seconds=0)
    rig.source.recorder = rig.recorder
    await rig.source.start()
    have = fixture("trade.json")
    # the feed recorded this trade a moment ago, in the hour that is still open
    rig.recorder.write(
        "trade",
        {
            "received_ns": LATE,
            "ts_ms": 1,
            "ticker": "M1",
            "trade_id": have["trade_id"],
            "yes_price_e6": 220_000,
            "no_price_e6": 780_000,
            "count_e2": 1250,
            "taker_side": "yes",
            "backfilled": False,
        },
    )
    missed = {**have, "trade_id": "00000000-0000-4000-8000-0000000000ff"}
    rig.kalshi.trades = [have, missed]
    rig.state.add_gap("trade", "reconnect", LATE - 10 * 10**9, LATE, "backfill", LATE)
    await rig.source._backfill_open_gaps()
    assert rig.state.gaps("done")[0].detail == "1 trades fetched"
    await rig.source.stop()
    ids = read_rows(hours(tmp_path / "rec", "trade"), "trade").column("trade_id").to_pylist()
    assert sorted(ids) == sorted([have["trade_id"], missed["trade_id"]])  # each exactly once


async def test_a_restart_gap_runs_until_the_subscriptions_are_live(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    rig.state.beat({"trade": T0 - 30 * 10**9})
    rig.kalshi.on_markets = lambda: rig.clock.advance_to(rig.clock.now_ns() + 60 * 10**9)
    await rig.source.start()  # the full discovery's two listings take two minutes
    gap = rig.state.gaps()[0]
    assert gap.start_ns == T0 - 30 * 10**9 and gap.end_ns == T0 + 120 * 10**9
    await rig.source.stop()


async def test_a_discovery_cycle_leaves_later_gaps_open(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    rig.state.add_gap("lifecycle", "sequence", T0, T0 + 10**12, "discovery", T0)  # ends later
    await rig.source._discover()
    assert len(rig.state.gaps("open")) == 1
    await rig.source.stop()


# --- the M1.4b merge review's findings ----------------------------------------------------------

HOUR_NS = 3_600 * 10**9


async def test_snapshots_go_to_the_book_subscription_as_it_is_now(tmp_path: Path) -> None:
    """Review 1: the book's sid changes with every reconnect; a remembered one is stale."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    old = rig.feed.sid_of("orderbook_delta")
    assert old is not None
    rig.feed.subscriptions[99] = rig.feed.subscriptions.pop(old)  # a reconnect renumbers it
    await rig.feed.queue.put(SequenceGap(99, 5, 9, 1))
    await rig.feed.queue.put(frame("ticker", TICKER_MSG, T0 + 3, sid=1))
    await rig.items(1)
    await asyncio.sleep(0)
    assert rig.feed.snapshot_sids == [99]
    await rig.source.stop()


async def test_a_frame_that_fails_is_a_gap_and_the_feed_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2: one bad frame used to end the pump without a word."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    real = rig.recorder.write

    def failing(channel: str, row: dict[str, Any]) -> None:
        if str(row.get("trade_id", "")).startswith("bad"):
            raise ValueError("bad row")
        real(channel, row)

    monkeypatch.setattr(rig.recorder, "write", failing)
    for trade_id in ("bad1", "bad2", "ok"):
        await rig.feed.queue.put(
            frame("trade", {**TRADE_MSG, "trade_id": trade_id}, T0 + 1, seq=1, sid=2)
        )
    items = await rig.items(1)
    assert items[0].payload["trade_id"] == "ok"
    assert rig.metrics.counters["kalshi.pump.errors"] == 2
    errors = [g for g in rig.state.gaps() if g.reason == "processing error"]
    assert [g.channel for g in errors] == ["trade"]  # at most one a minute per channel
    await rig.source.stop()


async def test_downtime_after_a_clean_stop_is_logged_too(tmp_path: Path) -> None:
    """Review 3: a clean stop was a loss nobody logged, and a crash during start looked clean."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    await rig.feed.queue.put(frame("trade", TRADE_MSG, T0 + 5, seq=1, sid=2))
    await rig.items(1)
    await rig.source.stop()
    assert rig.state.shut_down_cleanly()
    later = Rig(tmp_path, [market("M1")])
    later.clock.advance_to(T0 + 600 * 10**9)
    await later.source.start()
    assert not later.state.shut_down_cleanly()
    stopped = [g for g in later.state.gaps() if g.reason == "stopped"]
    assert [(g.channel, g.start_ns, g.end_ns) for g in stopped] == [
        ("trade", T0 + 5, T0 + 600 * 10**9)
    ]
    await later.source.stop()


async def test_a_market_is_asked_for_one_snapshot_until_it_comes(tmp_path: Path) -> None:
    """Review 4: the request ran on the pump (stalling it) and was repeated for every gap."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    sid = rig.feed.sid_of("orderbook_delta")
    assert sid is not None
    await rig.feed.queue.put(SequenceGap(sid, 5, 9, 1))
    await rig.feed.queue.put(SequenceGap(sid, 9, 12, 1))  # still waiting: not asked again
    await rig.feed.queue.put(frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + 1, seq=12, sid=sid))
    await rig.items(1)
    await rig.feed.queue.put(SequenceGap(sid, 13, 15, 1))  # it came: free to ask again
    await rig.feed.queue.put(frame("ticker", TICKER_MSG, T0 + 3, sid=1))
    await rig.items(1)
    await asyncio.sleep(0)
    assert rig.feed.snapshots == [["M1"], ["M1"]]
    await rig.source.stop()


async def test_a_repeated_backfill_never_stores_a_trade_twice(tmp_path: Path) -> None:
    """Review 5: an earlier attempt's rows carry the time they were fetched, in a later hour."""
    rig = Rig(tmp_path, [market("M1")])
    rig.recorder = Recorder(tmp_path / "rec", flush_seconds=0)
    rig.source.recorder = rig.recorder
    await rig.source.start()
    rig.kalshi.trades = [fixture("trade.json")]
    rig.clock.advance_to(LATE + 2 * HOUR_NS)
    assert await rig.source._backfill(LATE - 10 * 10**9, LATE) == 1
    rig.clock.advance_to(LATE + 3 * HOUR_NS)  # the attempt is repeated an hour later
    assert await rig.source._backfill(LATE - 10 * 10**9, LATE) == 0
    await rig.source.stop()
    assert read_rows(hours(tmp_path / "rec", "trade"), "trade").num_rows == 1


async def test_a_quiet_channel_has_no_empty_loss(tmp_path: Path) -> None:
    """Review 6: a channel with no frame yet had its loss start "now", an empty gap."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()  # live at T0
    rig.clock.advance_to(T0 + 100 * 10**9)
    await rig.feed.queue.put(Reconnected(2, "connection_lost", 3.0))
    await rig.feed.queue.put(frame("ticker", TICKER_MSG, T0 + 101 * 10**9, sid=1))
    await rig.items(1)
    lifecycle = next(g for g in rig.state.gaps() if g.channel == "lifecycle")
    assert (lifecycle.start_ns, lifecycle.end_ns) == (T0, T0 + 100 * 10**9)
    await rig.source.stop()


async def test_a_refused_subscription_is_sent_again_until_it_holds(tmp_path: Path) -> None:
    """Review 7: a subscription refused after a reconnect was never sent again."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    rig.feed.refuse_resubscribe = 1
    await rig.feed.queue.put(ResubscribeFailed("trade", "refused", 2))
    await rig.feed.queue.put(ResubscribeFailed("trade", "refused", 2))  # already being retried
    await rig.feed.queue.put(frame("ticker", TICKER_MSG, T0 + 3, sid=1))
    await rig.items(1)
    for _ in range(400):
        if any(g.reason == "resubscribed" for g in rig.state.gaps()):
            break
        await asyncio.sleep(0.005)
    assert rig.feed.resubscribed == ["trade", "trade"]  # refused once, then held
    assert rig.metrics.counters["kalshi.resubscribe.errors"] == 1
    assert [g.channel for g in rig.state.gaps() if g.reason == "resubscribed"] == ["trade"]
    await rig.source.stop()


async def test_the_rebalance_removes_before_it_adds_and_trusts_the_exchange(
    tmp_path: Path,
) -> None:
    """Review 8: adding first went past the cap; a failed step left a set the exchange lacked."""
    rig = Rig(tmp_path, [market("A", volume="10.00"), market("B", volume="5.00")])
    await rig.source.start()
    rig.feed.cap = 2
    rig.kalshi.updated = [market("C", volume="99.00"), market("B", status="closed")]
    await rig.source._discover()
    await rig.source._rebalance()
    assert [action for _, action, _ in rig.feed.updates] == ["delete_markets", "add_markets"]
    assert rig.source.watched.current == {"A", "C"}
    rig.feed.cap = 1
    rig.kalshi.updated = [market("D", volume="999.00")]
    await rig.source._discover()
    with pytest.raises(FeedError):
        await rig.source._rebalance()  # A goes; adding D is refused
    assert rig.source.watched.current == {"C"}
    await rig.source.stop()


async def test_the_heartbeat_compacts_finished_hours_after_the_grace(tmp_path: Path) -> None:
    """Review 9: compaction ran on the event loop, in the pump, at every hour boundary."""
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    await rig.feed.queue.put(frame("trade", TRADE_MSG, T0 + 5, seq=1, sid=2))
    await rig.items(1)
    rig.clock.advance_to(T0 + HOUR_NS + 60 * 10**9)
    await rig.source._heartbeat()
    assert hours(tmp_path / "rec", "trade") == []  # still in its grace period
    rig.clock.advance_to(T0 + HOUR_NS + 120 * 10**9)
    await rig.source._heartbeat()
    assert len(hours(tmp_path / "rec", "trade")) == 1
    await rig.source.stop()
