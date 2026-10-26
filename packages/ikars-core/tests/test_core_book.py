"""The order-book messages and the in-memory book, on hand-written frames with made-up values,
shaped as Kalshi's WebSocket reference documents them (read 2026-10-26)."""

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ikars_core.book import Applied, OrderBook
from ikars_core.ws_models import (
    ORDERBOOK_DELTA,
    ORDERBOOK_SNAPSHOT,
    OrderbookDeltaMsg,
    OrderbookSnapshotMsg,
    parse_frame,
)

M = "KXDEMO-26JAN01-T50"


def frame(kind: str, msg: dict[str, Any], seq: int = 1) -> str:
    return json.dumps({"type": kind, "sid": 2, "seq": seq, "sending_ts_ms": 1, "msg": msg})


def snapshot(clock: int | None = 100, **sides: Any) -> OrderbookSnapshotMsg:
    body = {"market_ticker": M, "market_id": "id", "clock": clock, **sides}
    payload = parse_frame(frame(ORDERBOOK_SNAPSHOT, body)).payload
    assert isinstance(payload, OrderbookSnapshotMsg)
    return payload


def delta(price: str, size: str, side: str = "yes", clock: int = 101) -> OrderbookDeltaMsg:
    body = {
        "market_ticker": M,
        "market_id": "id",
        "clock": clock,
        "price_dollars": price,
        "delta_fp": size,
        "side": side,
        "ts_ms": 5,
    }
    payload = parse_frame(frame(ORDERBOOK_DELTA, body)).payload
    assert isinstance(payload, OrderbookDeltaMsg)
    return payload


BOOK: dict[str, Any] = {
    "yes_dollars_fp": [["0.0800", "300.00"], ["0.2200", "333.00"]],
    "no_dollars_fp": [["0.5400", "20.00"]],
}


# --- the messages -------------------------------------------------------------------------------


def test_a_snapshot_is_read_in_fixed_point() -> None:
    s = snapshot(**BOOK)
    assert s.yes == ((80_000, 30_000), (220_000, 33_300)) and s.no == ((540_000, 2_000),)
    assert s.clock == 100


def test_a_side_with_no_bids_is_absent_and_reads_as_empty() -> None:
    assert snapshot().yes == () == snapshot().no


def test_a_delta_is_read_with_a_signed_size() -> None:
    d = delta("0.9600", "-54.00")
    assert (d.price_e6, d.delta_e2, d.side, d.clock, d.ts_ms) == (960_000, -5_400, "yes", 101, 5)


@pytest.mark.parametrize(
    "bad",
    [{"yes_dollars_fp": [["0.08"]]}, {"yes_dollars_fp": [[8, "1.00"]]},
     {"yes_dollars_fp": "x"}, {"yes_dollars_fp": [["0.0800001", "1.00"]]}],
)  # fmt: skip
def test_a_malformed_level_is_kept_with_an_error(bad: dict[str, Any]) -> None:
    result = parse_frame(frame(ORDERBOOK_SNAPSHOT, {"market_ticker": M, "clock": 1, **bad}))
    assert result.payload is None and result.error and result.raw["msg"]["market_ticker"] == M


def test_a_delta_on_an_unknown_side_is_an_error() -> None:
    body = {"market_ticker": M, "clock": 1, "price_dollars": "0.5", "delta_fp": "1", "side": "up"}
    assert parse_frame(frame(ORDERBOOK_DELTA, body)).error


# --- the book -----------------------------------------------------------------------------------


def test_deltas_change_levels_and_a_level_at_zero_is_removed() -> None:
    book = OrderBook(M)
    assert book.apply_snapshot(snapshot(**BOOK)) is Applied.APPLIED and book.ready
    assert book.apply_delta(delta("0.2200", "-333.00")) is Applied.APPLIED
    assert book.apply_delta(delta("0.3000", "5.00", clock=102)) is Applied.APPLIED
    assert book.bids("yes") == [(300_000, 500), (80_000, 30_000)]
    assert book.clock == 102


def test_best_asks_come_from_the_other_side() -> None:
    book = OrderBook(M)
    book.apply_snapshot(snapshot(**BOOK))
    assert book.best_bid("yes") == (220_000, 33_300)
    assert book.best_ask("yes") == (460_000, 2_000)  # $1 - the best NO bid of 54 cents
    assert book.best_ask("no") == (780_000, 33_300)
    assert not book.crossed
    assert OrderBook(M).best_ask("yes") is None


def test_a_crossed_book_is_reported() -> None:
    book = OrderBook(M)
    book.apply_snapshot(
        snapshot(yes_dollars_fp=[["0.6000", "1.00"]], no_dollars_fp=[["0.4100", "1.00"]])
    )
    assert book.crossed


def test_a_delta_already_in_the_snapshot_is_stale() -> None:
    book = OrderBook(M)
    book.apply_snapshot(snapshot(**BOOK))
    assert book.apply_delta(delta("0.2200", "-1.00", clock=100)) is Applied.STALE
    assert book.best_bid("yes") == (220_000, 33_300)


def test_deltas_may_share_a_clock() -> None:
    book = OrderBook(M)
    book.apply_snapshot(snapshot(**BOOK))
    assert book.apply_delta(delta("0.1000", "1.00", clock=105)) is Applied.APPLIED
    assert book.apply_delta(delta("0.1100", "1.00", clock=105)) is Applied.APPLIED


def test_a_delta_before_any_snapshot_is_ignored() -> None:
    book = OrderBook(M)
    assert book.apply_delta(delta("0.5000", "1.00")) is Applied.NO_SNAPSHOT
    assert not book.ready and book.yes == {}


def test_a_missed_message_breaks_the_book_until_the_next_snapshot() -> None:
    book = OrderBook(M)
    book.apply_snapshot(snapshot(**BOOK))
    assert book.apply_delta(delta("0.0800", "-301.00")) is Applied.BROKEN
    assert not book.ready
    assert book.apply_delta(delta("0.5000", "1.00", clock=103)) is Applied.BROKEN
    assert book.apply_snapshot(snapshot(clock=200, **BOOK)) is Applied.APPLIED and book.ready


def test_another_markets_messages_are_refused() -> None:
    book = OrderBook("OTHER")
    assert book.apply_snapshot(snapshot(**BOOK)) is Applied.WRONG_MARKET
    assert book.apply_delta(delta("0.5000", "1.00")) is Applied.WRONG_MARKET


PRICES = st.integers(min_value=1, max_value=99).map(lambda c: f"0.{c:02d}00")


@given(st.lists(st.tuples(PRICES, st.integers(min_value=-500, max_value=500), st.booleans())))
def test_valid_deltas_leave_the_book_equal_to_their_sum(
    changes: list[tuple[str, int, bool]],
) -> None:
    book = OrderBook(M)
    book.apply_snapshot(snapshot())
    expected: dict[tuple[str, int], int] = {}
    for clock, (price, hundredths, yes) in enumerate(changes, start=101):
        side = "yes" if yes else "no"
        key = (side, int(price[2:4]) * 10_000)
        if expected.get(key, 0) + hundredths < 0:
            continue  # a delta that would go negative is a missed message, tested above
        expected[key] = expected.get(key, 0) + hundredths
        size = f"{hundredths / 100:.2f}"
        assert book.apply_delta(delta(price, size, side, clock)) is Applied.APPLIED
    for side in ("yes", "no"):
        want = sorted(((p, n) for (s, p), n in expected.items() if s == side and n), reverse=True)
        assert book.bids(side) == want


# --- as production sent them on 2026-10-26: no clock on either message ---------------------------

PRODUCTION_SNAPSHOT = {"market_ticker": M, "market_id": "id", **BOOK}
PRODUCTION_DELTA = {
    "market_ticker": M,
    "market_id": "id",
    "price_dollars": "0.6800",
    "delta_fp": "-10.00",
    "side": "no",
    "ts": "2026-01-01T00:00:00.000Z",
    "ts_ms": 1767225600000,
}


def test_book_messages_without_a_clock_parse() -> None:
    s = parse_frame(frame(ORDERBOOK_SNAPSHOT, PRODUCTION_SNAPSHOT)).payload
    d = parse_frame(frame(ORDERBOOK_DELTA, PRODUCTION_DELTA, seq=2)).payload
    assert isinstance(s, OrderbookSnapshotMsg) and s.clock is None
    assert isinstance(d, OrderbookDeltaMsg) and d.clock is None and d.delta_e2 == -1_000


def test_without_clocks_every_delta_after_the_snapshot_applies() -> None:
    book = OrderBook(M)
    s = parse_frame(frame(ORDERBOOK_SNAPSHOT, PRODUCTION_SNAPSHOT)).payload
    assert isinstance(s, OrderbookSnapshotMsg)
    assert book.apply_snapshot(s) is Applied.APPLIED and book.ready
    raise_no = {**PRODUCTION_DELTA, "price_dollars": "0.5400", "delta_fp": "5.00"}
    d = parse_frame(frame(ORDERBOOK_DELTA, raise_no, seq=2)).payload
    assert isinstance(d, OrderbookDeltaMsg)
    assert book.apply_delta(d) is Applied.APPLIED
    assert book.best_bid("no") == (540_000, 2_500) and book.clock is None


def test_a_delta_with_a_clock_after_a_snapshot_without_one_applies() -> None:
    book = OrderBook(M)
    book.apply_snapshot(snapshot(clock=None, **BOOK))
    assert book.apply_delta(delta("0.3000", "1.00", clock=5)) is Applied.APPLIED
    assert book.clock == 5
