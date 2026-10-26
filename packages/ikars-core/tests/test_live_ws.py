"""Opt-in checks of the signing and the WebSocket feed against the real Kalshi service.

    $env:IKARS_LIVE = '1'      # the key: IKARS_KALSHI_KEY_ID and IKARS_KALSHI_PRIVATE_KEY_PATH
    pytest packages/ikars-core/tests/test_live_ws.py -q

Read-only: one signed GET and short subscriptions to the ticker, trade, lifecycle and order-book
channels of a few busy markets. Nothing is written to Kalshi. They ask what a fake cannot: does
Kalshi accept our signature, do real frames parse into the models without errors, do ``seq`` and
``sid`` behave as assumed, and does a reconnect restore the subscription.

The key id and path are never printed. A summary of counts and field *names* (never an account
value) goes to the file named by ``IKARS_LIVE_SUMMARY``; the raw public frames go to the file named
by ``IKARS_LIVE_FRAMES``, if set, for building fixtures. Neither belongs in the repository.
"""

import asyncio
import bisect
import json
import os
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from ikars_core.auth import AuthError, DataSigner
from ikars_core.book import Applied, OrderBook
from ikars_core.config import REST_URLS, WS_URLS, Environment
from ikars_core.ws import Data, FeedConfig, KalshiFeed, Reconnected, SequenceGap
from ikars_core.ws_models import (
    ORDERBOOK_CHANNEL,
    OrderbookDeltaMsg,
    OrderbookSnapshotMsg,
    TickerMsg,
)

pytestmark = pytest.mark.integration

LINES: list[str] = []
SECONDS = float(os.environ.get("IKARS_LIVE_WS_SECONDS", "30"))
BASE = REST_URLS[Environment.PRODUCTION]


@pytest.fixture(scope="module", autouse=True)
def summary() -> Iterator[None]:
    yield
    target = os.environ.get("IKARS_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def signer() -> DataSigner:
    try:
        return DataSigner.from_environment()
    except AuthError:
        pytest.skip("no usable data key in IKARS_KALSHI_KEY_ID and IKARS_KALSHI_PRIVATE_KEY_PATH")


def busiest_markets(count: int) -> list[str]:
    """The markets with the most trades in the last thousand: they are trading right now."""
    response = httpx.get(f"{BASE}/markets/trades", params={"limit": 1000}, timeout=30)
    assert response.status_code == 200
    counts = Counter(t["ticker"] for t in response.json()["trades"] if "KXMVE" not in t["ticker"])
    assert counts, "no recent trades to follow"
    return [ticker for ticker, _ in counts.most_common(count)]


def test_the_data_key_is_accepted_for_a_read(signer: DataSigner) -> None:
    path = "/trade-api/v2/account/limits"
    response = httpx.get(
        "https://external-api.kalshi.com" + path, headers=signer.headers("GET", path), timeout=30
    )
    LINES.append(f"signed GET {path}: HTTP {response.status_code}")
    if response.status_code == 200:
        LINES.append(f"fields in the reply: {sorted(response.json())}")
    assert response.status_code == 200


async def test_real_frames_parse_and_sequences_behave(signer: DataSigner) -> None:
    tickers = busiest_markets(5)
    raw_frames: list[dict[str, Any]] = []
    types: Counter[str] = Counter()
    seq_seen: Counter[str] = Counter()
    seen_fields: dict[str, set[str]] = {}
    delays: list[float] = []
    gaps = 0
    errors: list[str] = []
    async with KalshiFeed(signer, FeedConfig(url=WS_URLS[Environment.PRODUCTION])) as feed:
        await feed.subscribe("ticker", tickers)
        await feed.subscribe("trade", tickers)
        await feed.subscribe("market_lifecycle_v2")
        events = feed.events()
        deadline = time.monotonic() + SECONDS
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(anext(events), timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            if isinstance(event, SequenceGap):
                gaps += 1
                continue
            if not isinstance(event, Data):
                continue
            frame = event.frame
            types[frame.type] += 1
            raw_frames.append({"received_ns": event.received_ns, "frame": frame.raw})
            if frame.seq is not None:
                seq_seen[frame.type] += 1
            if frame.error:
                errors.append(frame.error)
            message = frame.raw.get("msg")
            if isinstance(message, dict):
                seen_fields.setdefault(frame.type, set()).update(message)
            if frame.sending_ts_ms:
                delays.append(event.received_ns / 1e6 - frame.sending_ts_ms)
        stats = feed.stats()
    LINES.append(f"frames in {SECONDS:g} s: {dict(types)}")
    LINES.append(f"frames that carried seq, by type: {dict(seq_seen)}; gaps seen: {gaps}")
    for kind, names in sorted(seen_fields.items()):
        LINES.append(f"fields seen in {kind}: {sorted(names)}")
    if delays:
        delays.sort()
        LINES.append(
            f"receive minus sending_ts_ms (ms): min {delays[0]:.0f}, "
            f"median {delays[len(delays) // 2]:.0f}, max {delays[-1]:.0f}"
        )
    LINES.append(f"parse errors by type: {stats['parse_errors_by_type']}; first: {errors[:2]}")
    LINES.append(f"unknown fields by type: {stats['unknown_fields_by_type']}")
    target = os.environ.get("IKARS_LIVE_FRAMES")
    if target:
        text = "\n".join(json.dumps(f) for f in raw_frames) + "\n"
        await asyncio.to_thread(Path(target).write_text, text, encoding="utf-8")
    assert types, "no frames arrived"
    assert not errors, f"{len(errors)} frames did not fit their models"
    assert stats["bad_frames"] == 0


async def test_a_dropped_link_is_restored_by_the_feed(signer: DataSigner) -> None:
    tickers = busiest_markets(3)
    config = FeedConfig(url=WS_URLS[Environment.PRODUCTION], reconnect_base=0.5)
    async with KalshiFeed(signer, config) as feed:
        await feed.subscribe("ticker", tickers)
        life_sid = await feed.subscribe("market_lifecycle_v2")
        events = feed.events()
        before: list[int] = []
        deadline = time.monotonic() + 30
        while not before and time.monotonic() < deadline:  # wait for a lifecycle frame
            event = await asyncio.wait_for(anext(events), timeout=deadline - time.monotonic())
            if isinstance(event, Data) and event.frame.type == "market_lifecycle_v2":
                before.append(event.frame.seq or 0)
        await feed._conn.close()  # noqa: SLF001 (cut the link as the network would)
        seen_reconnect, gaps = False, 0
        after: list[int] = []
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and not after:
            event = await asyncio.wait_for(anext(events), timeout=deadline - time.monotonic())
            if isinstance(event, Reconnected):
                seen_reconnect = True
                LINES.append(f"reconnect: generation {event.generation}, reason {event.reason}")
            elif isinstance(event, SequenceGap):
                gaps += 1
            elif isinstance(event, Data) and seen_reconnect:
                if event.frame.type == "market_lifecycle_v2":
                    after.append(event.frame.seq or 0)
                    LINES.append(
                        f"lifecycle sid before {life_sid} seq {before[-1]}; "
                        f"after the reconnect sid {event.frame.sid} seq {event.frame.seq}"
                    )
        LINES.append(f"gaps reported across the reconnect: {gaps}")
    assert seen_reconnect and after


async def test_adding_and_removing_markets_does_not_look_like_a_gap(signer: DataSigner) -> None:
    """Whether a command's reply uses up a number of the subscription's seq, on a busy channel."""
    first, second = busiest_markets(4)[:2], busiest_markets(4)[2:]
    async with KalshiFeed(signer, FeedConfig(url=WS_URLS[Environment.PRODUCTION])) as feed:
        sid = await feed.subscribe("trade", first)
        events = feed.events()
        gaps: list[SequenceGap] = []
        frames_by_phase: Counter[str] = Counter()

        async def watch(phase: str, seconds: float) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                try:
                    event = await asyncio.wait_for(
                        anext(events), timeout=deadline - time.monotonic()
                    )
                except TimeoutError:
                    return
                if isinstance(event, SequenceGap):
                    gaps.append(event)
                elif isinstance(event, Data):
                    frames_by_phase[phase] += 1

        await watch("before", 6)
        added = await feed.update_subscription(sid, "add_markets", second)
        await watch("after adding", 8)
        removed = await feed.update_subscription(sid, "delete_markets", second)
        await watch("after removing", 8)
    LINES.append(
        f"update_subscription: market set {len(added)} after adding, {len(removed)} after removing"
    )
    LINES.append(
        f"trade frames by phase: {dict(frames_by_phase)}; "
        f"gaps: {[(g.expected, g.got) for g in gaps]}"
    )
    assert frames_by_phase["before"] > 0
    assert len(added) == len(first) + len(second) and len(removed) == len(first)
    assert not gaps


async def test_books_are_bids_and_stay_consistent(signer: DataSigner) -> None:
    """Keep the books of the busiest markets from the order-book channel and compare each ticker
    frame's best YES bid and ask with the book **as it was at the ticker's own time** (a busy book
    changes hundreds of times a second; the ticker comes about once a second). They agree only if
    each side really is bids (a YES ask being $1 minus the best NO bid). No book should break."""
    tickers = busiest_markets(3)
    books = {ticker: OrderBook(ticker) for ticker in tickers}
    history: dict[str, tuple[list[int], list[tuple[int, int] | None]]] = {
        ticker: ([], []) for ticker in tickers
    }  # per market: times (ms) and the (best YES bid, best YES ask) from then on
    applied: Counter[str] = Counter()
    deltas: Counter[str] = Counter()
    agree = disagree = crossed = 0

    def remember(ticker: str, at_ms: int) -> None:
        book = books[ticker]
        bid, ask = book.best_bid("yes"), book.best_ask("yes")
        times, states = history[ticker]
        times.append(at_ms)
        states.append((bid[0], ask[0]) if bid and ask else None)

    async with KalshiFeed(signer, FeedConfig(url=WS_URLS[Environment.PRODUCTION])) as feed:
        await feed.subscribe(ORDERBOOK_CHANNEL, tickers)
        await feed.subscribe("ticker", tickers)
        events = feed.events()
        deadline = time.monotonic() + SECONDS
        while time.monotonic() < deadline:
            try:
                event = await asyncio.wait_for(anext(events), timeout=deadline - time.monotonic())
            except TimeoutError:
                break
            if not isinstance(event, Data) or event.frame.payload is None:
                continue
            message = event.frame.payload
            if isinstance(message, OrderbookSnapshotMsg):
                applied[books[message.market_ticker].apply_snapshot(message)] += 1
                remember(message.market_ticker, event.frame.sending_ts_ms or 0)
            elif isinstance(message, OrderbookDeltaMsg):
                deltas[message.market_ticker] += 1
                result = books[message.market_ticker].apply_delta(message)
                applied[result] += 1
                if result is Applied.APPLIED and message.ts_ms is not None:
                    remember(message.market_ticker, message.ts_ms)
            elif isinstance(message, TickerMsg) and message.market_ticker in books:
                if message.ts_ms is None or message.yes_bid_e6 is None:
                    continue
                if message.yes_ask_e6 is None:
                    continue
                times, states = history[message.market_ticker]
                at = bisect.bisect_right(times, message.ts_ms) - 1
                if at < 0 or states[at] is None:
                    continue
                crossed += books[message.market_ticker].crossed
                if states[at] == (message.yes_bid_e6, message.yes_ask_e6):
                    agree += 1
                else:
                    disagree += 1
    rates = {t: round(n / SECONDS) for t, n in deltas.items()}
    LINES.append(f"order book over {SECONDS:g} s on {len(tickers)} markets: {dict(applied)}")
    LINES.append(f"book deltas a second, by market: {rates}")
    LINES.append(f"book at the ticker's time vs the ticker: {agree} agree, {disagree} differ")
    LINES.append(f"crossed books seen: {crossed}")
    assert applied[Applied.APPLIED] > len(tickers), "no delta was applied"
    assert applied[Applied.BROKEN] == 0
    assert agree + disagree >= 5, "too few ticker frames to compare"
    assert agree >= 0.8 * (agree + disagree)
