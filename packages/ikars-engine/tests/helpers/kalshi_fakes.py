"""Fakes for Kalshi tests, shared by more than one test module (M1.13: the replay tests run the
live source on them): a fake REST service on the core package's made-up fixtures, a fake feed
carrying real-shaped frames, and a rig that wires the source to both."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import KalshiPublicClient
from ikars_core.ws import Data, FeedError
from ikars_core.ws_models import parse_frame
from ikars_engine.clocks import VirtualClock
from ikars_engine.kalshi.discovery import Discovery, MarketIndex
from ikars_engine.kalshi.exchange import ExchangeWatch
from ikars_engine.kalshi.source import Intervals, KalshiSource
from ikars_engine.kalshi.watched import WatchedSet
from ikars_engine.metrics import Metrics
from ikars_engine.recorder import Recorder
from ikars_engine.skew import SkewMonitor
from ikars_engine.store import StateStore

FIXTURES = Path(__file__).parents[3] / "ikars-core" / "tests" / "fixtures"
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


def _seconds(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


class FakeKalshi:
    """The REST endpoints the source uses, with state the tests can change."""

    def __init__(self) -> None:
        self.markets: list[dict[str, Any]] = []
        self.historical: list[dict[str, Any]] = []  # markets past the cutoff (M1.14)
        self.updated: list[dict[str, Any]] = []  # returned for min_updated_ts queries
        self.status = fixture("exchange_status.json")
        self.trades: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.fail = False  # every request answers 404
        self.on_markets: Any = None  # called on each market listing (to let time pass)
        self.trade_asks: list[str] = []  # each trades request's min_ts
        self.fail_trade_ask: int | None = None  # this trades request (1 is the first) fails
        self.trades_by_time = (
            False  # answer only the trades in the request's window, as Kalshi does
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            return httpx.Response(404)
        path = request.url.path.removeprefix("/trade-api/v2")
        params = request.url.params
        body: dict[str, Any]
        if path == "/series":
            body = {"series": [fixture("series.json")]}
        elif path == "/markets" and "tickers" in params:  # a lookup by name (M1.14)
            wanted = set(params["tickers"].split(","))
            body = {"markets": [m for m in self.markets if m["ticker"] in wanted], "cursor": ""}
        elif path.startswith("/markets/") and path.count("/") == 2 and path != "/markets/trades":
            found = next((m for m in self.markets if m["ticker"] == path.split("/")[2]), None)
            if found is None:
                return httpx.Response(404, content=b'{"error": "not found"}')
            body = {"market": found}
        elif path.startswith("/historical/markets/") and path.count("/") == 3:
            found = next((m for m in self.historical if m["ticker"] == path.split("/")[3]), None)
            if found is None:
                return httpx.Response(404, content=b'{"error": "not found"}')
            body = {"market": found}
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
            status = params.get("status")
            if status in ("unopened", "closed", "settled"):  # as Kalshi filters (M1.14)
                body["markets"] = [m for m in self.markets if m["status"] == status]
            else:
                body["markets"] = [
                    m for m in self.markets if m["status"] not in ("unopened", "closed", "settled")
                ]
        elif path == "/exchange/status":
            body = self.status
        elif path == "/exchange/schedule":
            body = {"schedule": fixture("exchange_schedule.json")}
        elif path == "/historical/cutoff":
            body = fixture("cutoff.json")
        elif path in ("/markets/trades", "/historical/trades"):
            self.trade_asks.append(params["min_ts"])
            if len(self.trade_asks) == self.fail_trade_ask:
                return httpx.Response(400)
            trades = self.trades
            if self.trades_by_time:
                low, high = int(params["min_ts"]), int(params["max_ts"])
                trades = [t for t in trades if low <= _seconds(t["created_time"]) < high]
            body = {"trades": trades, "cursor": ""}
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
