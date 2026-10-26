"""Opt-in live checks of what Kalshi's documentation does not say (GET only, no key).

    $env:IKARS_LIVE = '1'; $env:IKARS_LIVE_SUMMARY = "$env:TEMP\\ikars-live-summary.txt"
    python -m uv run pytest packages/ikars-core/tests/test_live_bounds.py -q

* Are ``min_ts`` and ``max_ts`` inclusive on the trades endpoints? Found by fetching a wide window
  as the reference and comparing narrow windows against it, trade by trade.
* How big may a page be, and what happens above that?
* Are cursors stable, and do pages overlap? In what order do trades come?

About 40 requests. Only counts and the rules found are written to the summary.
"""

import os
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData, TypedPage
from ikars_core.models import Trade
from ikars_core.rest import KalshiAPIError, KalshiPublicClient

pytestmark = pytest.mark.integration

Fetch = Callable[..., AsyncIterator[TypedPage[Trade]]]

LINES: list[str] = []
WIDE = 45  # seconds in the reference window
NARROW = 10  # seconds in the window under test


@pytest.fixture(scope="module", autouse=True)
def summary() -> Iterator[None]:
    yield
    target = os.environ.get("IKARS_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


@pytest.fixture
async def client() -> AsyncIterator[KalshiPublicClient]:
    async with KalshiPublicClient(KalshiConfig(environment=Environment.PRODUCTION)) as c:
        yield c
        LINES.append(
            f"  requests {c.stats.requests}, retries {c.stats.retries}, 429s {c.stats.rate_limited}"
        )


def second(trade: Trade) -> int:
    return int(trade.created_time.timestamp())  # whole seconds, rounded down


async def collect(pages: AsyncIterator[TypedPage[Trade]]) -> list[Trade]:
    trades: list[Trade] = []
    async for page in pages:
        trades += page.items
    return trades


async def measure_bounds(label: str, fetch: Fetch, start: int) -> tuple[bool, bool]:
    """Returns (min_ts inclusive, max_ts inclusive) for ``fetch(min_ts, max_ts)``."""
    reference = await collect(fetch(min_ts=start - 3, max_ts=start + WIDE))
    seen = {second(t) for t in reference}
    # pick a window whose edge seconds, and the seconds just outside them, all have trades
    a = next(
        s
        for s in range(start, start + WIDE - NARROW - 3)
        if {s - 1, s, s + NARROW, s + NARROW + 1} <= seen
    )
    narrow = await collect(fetch(min_ts=a, max_ts=a + NARROW))
    got = {second(t) for t in narrow}
    min_inclusive = a in got
    max_inclusive = (a + NARROW) in got
    assert (a - 1) not in got, f"{label}: a trade before min_ts came back"
    assert (a + NARROW + 1) not in got, f"{label}: a trade after max_ts came back"
    # the narrow result must be exactly the reference trades the rule predicts
    low = a if min_inclusive else a + 1
    high = a + NARROW if max_inclusive else a + NARROW - 1
    expected = {t.trade_id for t in reference if low <= second(t) <= high}
    assert {t.trade_id for t in narrow} == expected, (
        f"{label}: the window does not follow one clean rule"
    )
    LINES.append(
        f"{label}: reference window {len(reference)} trades over {len(seen)} seconds; "
        f"window of {NARROW} s gave {len(narrow)} trades; "
        f"min_ts {'INCLUSIVE' if min_inclusive else 'EXCLUSIVE'}, "
        f"max_ts {'INCLUSIVE' if max_inclusive else 'EXCLUSIVE'}"
    )
    return min_inclusive, max_inclusive


async def test_the_historical_trade_bounds(client: KalshiPublicClient) -> None:
    data = MarketData(client)
    cutoff = int((await data.historical_cutoff()).trades_created_ts.timestamp())
    rule = await measure_bounds("historical trades", data.historical_trades, cutoff - 600)
    assert rule == (True, False)  # min_ts inclusive, max_ts exclusive (measured 2026-10-22)


async def test_the_live_trade_bounds(client: KalshiPublicClient) -> None:
    data = MarketData(client)
    rule = await measure_bounds("live trades", data.trades, int(time.time()) - 900)
    assert rule == (True, False)  # the same rule on the live endpoint


async def test_page_sizes_and_what_happens_above_the_limit(client: KalshiPublicClient) -> None:
    for path in ("/historical/trades", "/markets/trades"):
        for limit in (1000, 1001, 2000, 0, -1):
            try:
                payload = await client.get_json(path, {"limit": limit})
                outcome = f"HTTP 200, {len(payload.get('trades', []))} trades"
            except KalshiAPIError as exc:
                outcome = f"HTTP {exc.status}: {exc.body[:80]!r}"
            LINES.append(f"{path} limit={limit}: {outcome}")
            assert outcome.startswith("HTTP 200") == (limit in (0, 1000)), outcome
    for limit in (1000, 1001):
        try:
            payload = await client.get_json(
                "/historical/markets", {"limit": limit, "mve_filter": "exclude"}
            )
            outcome = f"HTTP 200, {len(payload.get('markets', []))} markets"
        except KalshiAPIError as exc:
            outcome = f"HTTP {exc.status}: {exc.body[:80]!r}"
        LINES.append(f"/historical/markets limit={limit}: {outcome}")


async def test_cursors_are_stable_pages_do_not_overlap_and_order_is_newest_first(
    client: KalshiPublicClient,
) -> None:
    data = MarketData(client)
    cutoff = int((await data.historical_cutoff()).trades_created_ts.timestamp())
    window = {"min_ts": cutoff - 600, "max_ts": cutoff - 400}

    async def walk(fetch: Fetch, **window: int) -> tuple[list[Trade], list[str | None]]:
        trades: list[Trade] = []
        cursors: list[str | None] = []
        async for page in fetch(limit=200, **window):
            trades += page.items
            cursors.append(page.next_cursor)
            if len(cursors) == 4:
                break
        return trades, cursors

    first, first_cursors = await walk(data.historical_trades, **window)
    again, again_cursors = await walk(data.historical_trades, **window)
    ids = [t.trade_id for t in first]
    ordered = all(a.created_time >= b.created_time for a, b in zip(first, first[1:], strict=False))
    LINES.append(
        f"historical cursors: {len(first_cursors)} pages of up to 200; duplicates across pages "
        f"{len(ids) - len(set(ids))}; same trades and cursors on a second walk "
        f"{ids == [t.trade_id for t in again] and first_cursors == again_cursors}; "
        f"newest first across pages {ordered}"
    )
    assert len(ids) == len(set(ids))
    assert ids == [t.trade_id for t in again]
    assert first_cursors == again_cursors

    live_now = int(time.time())
    live, _ = await walk(data.trades, min_ts=live_now - 900, max_ts=live_now - 700)
    live_ids = [t.trade_id for t in live]
    live_ordered = all(
        a.created_time >= b.created_time for a, b in zip(live, live[1:], strict=False)
    )
    LINES.append(
        f"live trades: {len(live)} in 4 pages; duplicates {len(live_ids) - len(set(live_ids))}; "
        f"newest first across pages {live_ordered}"
    )
    assert len(live_ids) == len(set(live_ids))
    assert ordered
    assert live_ordered
