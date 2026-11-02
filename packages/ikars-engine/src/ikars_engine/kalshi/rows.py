"""Kalshi's feed as recorded rows, and the items and state built from them (M1.13; PLAN D19 G3).

**One row, one item** (owner, 2026-11-01). The live source turns each feed message into the row it
records, and builds the engine's item *from that row*, with the same functions a replay uses on the
recording. So a replay hands the engine exactly the items the live engine had, by construction: a
plugin can only ever read what was recorded.

**One path for the kept state.** ``KalshiState`` holds what the source keeps between messages (each
market's quote and last price, the watched books, when each changed) and changes it only from rows
and feed events, live or replayed alike:

- a book is built from a snapshot and its deltas; a delta that would take a level negative breaks
  it until the next snapshot (``OrderBook``);
- a **reconnect** clears every book (the resubscription brings fresh snapshots), and a **sequence
  gap** on the book channel breaks every book until its snapshot comes, so a gap never reads as a
  book with nothing in it (SPARQ's lesson; M1.13);
- a **start** begins with no books, as the live engine does.

Rows are plain dicts in the recorder's columns (``recorder.SCHEMAS``): money and counts as fixed-
point integers, times as integer nanoseconds received and Kalshi's milliseconds.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ikars_core.book import Applied, OrderBook
from ikars_core.markets import is_combo
from ikars_core.models import Trade
from ikars_core.ws_models import (
    MarketLifecycleMsg,
    OrderbookDeltaMsg,
    OrderbookSnapshotMsg,
    TickerMsg,
    TradeMsg,
)

from ikars_engine.clocks import exact_ns
from ikars_engine.model import Item, Ref

MS = 1_000_000
# the engine's item kind for each recorded channel: the feed's message types, as live
KIND = {
    "ticker": "ticker",
    "trade": "trade",
    "lifecycle": "market_lifecycle_v2",
    "book_snapshot": "orderbook_snapshot",
    "book_delta": "orderbook_delta",
}
_NOT_PAYLOAD = ("received_ns", "ticker")


# --- rows from the feed ------------------------------------------------------------------------


def ticker_row(message: TickerMsg, at: int) -> dict[str, Any]:
    return {
        "received_ns": at, "ts_ms": message.ts_ms, "ticker": message.market_ticker,
        "price_e6": message.price_e6, "yes_bid_e6": message.yes_bid_e6,
        "yes_ask_e6": message.yes_ask_e6, "yes_bid_size_e2": message.yes_bid_size_e2,
        "yes_ask_size_e2": message.yes_ask_size_e2, "volume_e2": message.volume_e2,
        "open_interest_e2": message.open_interest_e2,
    }  # fmt: skip


def trade_row(message: TradeMsg, at: int) -> dict[str, Any]:
    return {
        "received_ns": at, "ts_ms": message.ts_ms, "ticker": message.market_ticker,
        "trade_id": message.trade_id, "yes_price_e6": message.yes_price_e6,
        "no_price_e6": message.no_price_e6, "count_e2": message.count_e2,
        "taker_side": message.taker_side, "backfilled": False,
    }  # fmt: skip


def rest_trade_row(trade: Trade, at: int) -> dict[str, Any]:
    """A trade fetched by the backfill (never an item: the live engine never saw it as one)."""
    return {
        "received_ns": at, "ts_ms": exact_ns(trade.created_time) // MS,
        "ticker": trade.ticker, "trade_id": trade.trade_id, "yes_price_e6": trade.yes_price_e6,
        "no_price_e6": trade.no_price_e6, "count_e2": trade.count_e2,
        "taker_side": trade.taker_side, "backfilled": True,
    }  # fmt: skip


def lifecycle_row(message: MarketLifecycleMsg, raw: Any, at: int) -> dict[str, Any]:
    return {
        "received_ns": at, "ticker": message.market_ticker or "",
        "event_type": message.event_type, "result": message.result,
        "message": json.dumps(raw, default=str),
    }  # fmt: skip


def snapshot_rows(
    message: OrderbookSnapshotMsg, number: int, at: int, *, checkpoint: bool = False
) -> list[dict[str, Any]]:
    """One row per level; an empty book is one row with no side, so it is recorded too."""
    base = {"received_ns": at, "ticker": message.market_ticker, "snapshot": number,
            "clock": message.clock, "checkpoint": checkpoint}  # fmt: skip
    rows = [
        {**base, "side": side, "price_e6": price, "count_e2": count}
        for side, levels in (("yes", message.yes), ("no", message.no))
        for price, count in levels
    ]
    return rows or [{**base, "side": None, "price_e6": None, "count_e2": None}]


def book_snapshot(book: OrderBook) -> OrderbookSnapshotMsg:
    """A kept book as a snapshot message (the hourly checkpoint a replay starts from)."""
    return OrderbookSnapshotMsg.model_construct(
        market_ticker=book.market_ticker, clock=book.clock,
        yes=tuple(sorted(book.yes.items())), no=tuple(sorted(book.no.items())),
    )  # fmt: skip


def delta_row(message: OrderbookDeltaMsg, seq: int | None, at: int) -> dict[str, Any]:
    return {
        "received_ns": at, "ts_ms": message.ts_ms, "ticker": message.market_ticker, "seq": seq,
        "side": message.side, "price_e6": message.price_e6, "delta_e2": message.delta_e2,
        "clock": message.clock,
    }  # fmt: skip


# --- items from rows ---------------------------------------------------------------------------


def item_of(channel: str, row: dict[str, Any]) -> Item | None:
    """The engine's item for one recorded row (a book snapshot: ``snapshot_item``). None for a
    row the live engine never saw as an item (a backfilled trade)."""
    if channel == "trade" and row.get("backfilled"):
        return None
    payload = {k: v for k, v in row.items() if k not in _NOT_PAYLOAD}
    ts = row.get("ts_ms")
    return Item(
        ref=Ref(KIND[channel], row["ticker"]),
        source="kalshi",
        received_ns=row["received_ns"],
        published_ns=None if ts is None else ts * MS,
        payload=payload,
    )


def snapshot_item(rows: list[dict[str, Any]]) -> Item:
    """The item for one snapshot's rows (all one ticker, one number, one received time)."""
    first = rows[0]
    yes = [[r["price_e6"], r["count_e2"]] for r in rows if r["side"] == "yes"]
    no = [[r["price_e6"], r["count_e2"]] for r in rows if r["side"] == "no"]
    return Item(
        ref=Ref(KIND["book_snapshot"], first["ticker"]),
        source="kalshi",
        received_ns=first["received_ns"],
        payload={
            "snapshot": first["snapshot"],
            "clock": first["clock"],
            "yes": yes,
            "no": no,
            "checkpoint": bool(first["checkpoint"]),
        },  # fmt: skip
    )


def snapshot_message(rows: list[dict[str, Any]]) -> OrderbookSnapshotMsg:
    first = rows[0]
    return OrderbookSnapshotMsg.model_construct(
        market_ticker=first["ticker"], clock=first["clock"],
        yes=tuple((r["price_e6"], r["count_e2"]) for r in rows if r["side"] == "yes"),
        no=tuple((r["price_e6"], r["count_e2"]) for r in rows if r["side"] == "no"),
    )  # fmt: skip


def delta_message(row: dict[str, Any]) -> OrderbookDeltaMsg:
    return OrderbookDeltaMsg.model_construct(
        market_ticker=row["ticker"], clock=row["clock"], side=row["side"],
        price_e6=row["price_e6"], delta_e2=row["delta_e2"], ts_ms=row["ts_ms"],
    )  # fmt: skip


# --- the kept state ----------------------------------------------------------------------------


@dataclass
class KalshiState:
    """What the source keeps between messages, changed only from rows and feed events (live and
    replay alike). ``keep(ticker)``: whether a market's quote is kept (the live source: not a combo
    unless watched)."""

    keep: Callable[[str], bool] = lambda ticker: not is_combo(ticker)
    books: dict[str, OrderBook] = field(default_factory=dict)
    last_price_e6: dict[str, int] = field(default_factory=dict)
    quotes: dict[str, tuple[int | None, int | None]] = field(default_factory=dict)
    # for the API's stream (M1.7): when each market's quote and book last changed, and each
    # book's version, so only what changed is sent
    changed_ns: dict[str, int] = field(default_factory=dict)
    book_changed_ns: dict[str, int] = field(default_factory=dict)
    book_version: dict[str, int] = field(default_factory=dict)

    def ticker(self, row: dict[str, Any]) -> None:
        ticker, at = row["ticker"], row["received_ns"]
        # the ticker channel carries every market, combos included: combos are never kept, and
        # a market the index still lacks after a discovery cycle is forgotten then (M1.R 16)
        if not self.keep(ticker):
            return
        before = (self.quotes.get(ticker), self.last_price_e6.get(ticker))
        if row["price_e6"] is not None:
            self.last_price_e6[ticker] = row["price_e6"]
        if row["yes_bid_e6"] is not None or row["yes_ask_e6"] is not None:
            bid, ask = self.quotes.get(ticker, (None, None))
            self.quotes[ticker] = (
                bid if row["yes_bid_e6"] is None else row["yes_bid_e6"],
                ask if row["yes_ask_e6"] is None else row["yes_ask_e6"],
            )
        if (self.quotes.get(ticker), self.last_price_e6.get(ticker)) != before:
            self.changed_ns[ticker] = at  # a volume-only frame leaves the quote's time

    def snapshot(self, message: OrderbookSnapshotMsg, at: int) -> None:
        ticker = message.market_ticker
        self.books.setdefault(ticker, OrderBook(ticker)).apply_snapshot(message)
        self._book_changed(ticker, at)

    def delta(self, message: OrderbookDeltaMsg, at: int) -> Applied:
        """Apply a delta; the result says whether the book needs a snapshot (``NO_SNAPSHOT``,
        ``BROKEN``)."""
        ticker = message.market_ticker
        kept = self.books.get(ticker)  # only a snapshot makes a book (M1.R 11, 16)
        if kept is None:
            return Applied.NO_SNAPSHOT
        was_broken = kept.broken
        applied = kept.apply_delta(message)
        if applied is Applied.APPLIED or (applied is Applied.BROKEN and not was_broken):
            self._book_changed(ticker, at)  # only a real change is news (M1.R 13)
        return applied

    def feed_event(self, row: dict[str, Any]) -> None:
        """A start or reconnect clears every book; a sequence gap on the book channel breaks every
        book until its next snapshot; ``forget`` drops all that is kept for one market (the index no
        longer has it), ``unwatch`` its book (M1.13)."""
        event, at = row["event"], row["received_ns"]
        if event == "forget":
            for kept in (self.quotes, self.last_price_e6, self.changed_ns, self.books,
                         self.book_version, self.book_changed_ns):  # fmt: skip
                kept.pop(row["detail"], None)
        elif event == "unwatch":
            self.books.pop(row["detail"], None)
        elif event in ("start", "reconnect"):
            for ticker in list(self.books):
                self._book_changed(ticker, at)
            self.books.clear()
        elif event == "sequence_gap" and row["channel"] == "book":
            for ticker, book in self.books.items():
                if not book.broken:
                    book.broken = True
                    self._book_changed(ticker, at)

    def _book_changed(self, ticker: str, at: int) -> None:
        self.book_version[ticker] = self.book_version.get(ticker, 0) + 1
        self.book_changed_ns[ticker] = at
