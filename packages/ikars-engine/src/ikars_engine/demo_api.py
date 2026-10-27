"""``python -m ikars_engine demo-api``: the real API server on **made-up** data (M1.8a-web).

For the web terminal's screen tests (Playwright) and its load test (PLAN D20 J): ``--markets``
made-up markets whose quotes move ``--hz`` times a second, order books for the first few, a made-up
ledger and alert log, all in a temporary folder that is deleted at the end. It never reads the real
data folder, settings, keys or Kalshi.

On start it prints one JSON line: the port, a sign-in address (with a one-time code) and the
per-start token, so a test can open the terminal or call the API. The token belongs to this
throw-away server only. It runs until stopped (Ctrl+C, or the test ends the process) or for
``--seconds``.
"""

import asyncio
import contextlib
import json
import random
import tempfile
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ikars_core.book import OrderBook
from ikars_core.ws_models import parse_frame

from ikars_engine.api import ApiContext, ApiServer
from ikars_engine.kalshi.discovery import MarketIndex, MarketInfo
from ikars_engine.ledger import Entry, Ledger
from ikars_engine.metrics import Metrics
from ikars_engine.portfolio import Held, portfolio
from ikars_engine.session import Sessions
from ikars_engine.webserve import terminal_root

BOOKS = 20  # markets with an order book
CENT = 10_000  # in dollar millionths


def made_up_market(n: int) -> MarketInfo:
    event = f"KXDEMO-26JAN{n // 10:02d}"
    return MarketInfo(
        ticker=f"{event}-T{n % 10}", event_ticker=event, series_ticker="KXDEMO",
        category=("Demo", "Weather", "Economics")[n % 3], title=f"Made-up market {n}",
        status="active", close_time=None, result=None, volume_24h_e2=(n * 7919) % 100_000,
    )  # fmt: skip


def made_up_book(ticker: str, bid_e6: int, sid: int, seq: int) -> OrderBook:
    """A small book around ``bid_e6`` (Kalshi's books are bids on both sides)."""

    def levels(top: int, size: int) -> list[list[str]]:  # prices stay within 1c..99c
        prices = (top - k * CENT for k in range(5))
        return [[f"0.{p // CENT:02d}00", f"{size + k}.00"]
                for k, p in enumerate(prices) if CENT <= p <= 99 * CENT]  # fmt: skip

    yes = levels(bid_e6, 10)
    no = levels(1_000_000 - bid_e6 - 2 * CENT, 8)
    raw = {"type": "orderbook_snapshot", "sid": sid, "seq": seq,
           "msg": {"market_ticker": ticker, "yes_dollars_fp": yes,
                   "no_dollars_fp": no}}  # fmt: skip
    book = OrderBook(ticker)
    book.apply_snapshot(parse_frame(json.dumps(raw)).payload)  # type: ignore[arg-type]
    return book


class Market:
    """The made-up state the API reads, moved by ``tick``."""

    def __init__(self, count: int, seed: int = 7) -> None:
        self.random = random.Random(seed)  # noqa: S311 (made-up prices, not security)
        self.index = MarketIndex()
        self.quotes: dict[str, tuple[int | None, int | None]] = {}
        self.last: dict[str, int] = {}
        self.changed: dict[str, int] = {}
        self.books: dict[str, OrderBook] = {}
        self.book_version: dict[str, int] = {}
        self.book_changed: dict[str, int] = {}
        for n in range(count):
            info = made_up_market(n)
            self.index.markets[info.ticker] = info
            bid = self.random.randrange(5, 90) * CENT
            self.quotes[info.ticker] = (bid, bid + 2 * CENT)
            self.last[info.ticker] = bid + CENT
        self.index.version += 1
        self.tick(time.time_ns())

    def tick(self, now_ns: int) -> None:
        for n, (ticker, (old, _ask)) in enumerate(list(self.quotes.items())):
            bid = min(97 * CENT, max(CENT, (old or CENT) + self.random.choice((-CENT, 0, CENT))))
            self.quotes[ticker] = (bid, bid + 2 * CENT)
            self.changed[ticker] = now_ns
            if n < BOOKS:
                version = self.book_version.get(ticker, 0) + 1
                self.books[ticker] = made_up_book(ticker, bid, 1, version)
                self.book_version[ticker] = version
                self.book_changed[ticker] = now_ns


def demo_health(market: Market) -> dict[str, Any]:
    return {
        "kalshi": {"feed": {"connected": True, "seconds_since_last_frame": 0.1},
                   "open_gaps": 0, "books_ready": len(market.books),
                   "watched": {"markets": len(market.books)}, "trading_active": True,
                   "storage": {"state": "ok"}},
        "ledger": {"verified": True, "reconciled_exact": True, "records": 3, "errors": 0},
        "backup": {"configured": False, "hours_since": None, "last_error": None},
        "clock": {"status": "ok", "offset_ms": 1.5}, "gpu": {"device": "Made-up GPU"},
        "plugins": {}, "demo": True,
    }  # fmt: skip


async def serve(
    folder: Path, markets: int, hz: float, port: int, seconds: float | None, out: Any = print
) -> None:
    market = Market(markets)
    ledger = Ledger(folder / "ledger.sqlite", ZoneInfo("America/Denver"))
    for n in range(3):
        ledger.append(Entry("deposit", time.time_ns() - (3 - n) * 10**9, "deposit", f"d{n}",
                            cash_e6=100_000_000))  # fmt: skip
    alerts = folder / "alerts.jsonl"
    alerts.write_text(json.dumps({"key": "demo", "priority": "high", "title": "made up",
                                  "text": "a made-up alert", "at": "2026-01-01T05:00:00-07:00",
                                  "outcome": "shown"}) + "\n", encoding="utf-8")  # fmt: skip
    sessions = Sessions(folder / "session", 1)
    held = Held({}, 300_000_000)

    def context_for(token: str) -> ApiContext:
        api = ApiContext(
            token=token, health=lambda: {**demo_health(market), "api": server.health()},
            index=market.index, quotes=market.quotes, last_price=market.last,
            changed_ns=market.changed, book_changed_ns=market.book_changed, books=market.books,
            book_version=market.book_version, watched=lambda: set(market.books), ledger=ledger,
            ledger_health=lambda: {"verified": True, "reconciled_exact": True},
            portfolio=lambda: portfolio(market.quotes, lambda s: None, lambda s: None, held),
            alert_log=alerts, metrics=Metrics(), now_ns=time.time_ns, sessions=sessions,
            web_root=terminal_root(),
        )  # fmt: skip
        return api

    server = ApiServer(context_for, folder, port=port)
    await server.start()
    token = server.ctx.token if server.ctx is not None else ""
    url = f"http://127.0.0.1:{server.port}/#code={sessions.issue_code()}"
    out(json.dumps({"port": server.port, "url": url, "token": token}), flush=True)
    began = time.monotonic()
    try:
        while seconds is None or time.monotonic() - began < seconds:
            await asyncio.sleep(1 / hz)
            market.tick(time.time_ns())
    finally:
        await server.stop()
        ledger.close()


def main(markets: int, hz: float, port: int, seconds: float | None) -> int:
    with (
        tempfile.TemporaryDirectory(prefix="ikars-demo-") as folder,
        contextlib.suppress(KeyboardInterrupt),
    ):
        asyncio.run(serve(Path(folder), markets, hz, port, seconds))
    return 0
