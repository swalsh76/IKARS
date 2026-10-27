"""Opt-in checks against the real Kalshi public API (GET only, no key, about 15 requests).

    $env:IKARS_LIVE = '1'
    python -m uv run pytest packages/ikars-core/tests/test_live_public.py -q

They ask whether the models accept what Kalshi really sends, which a fixture cannot show. They
use the production environment explicitly (the demo site has no history). A short summary of
counts, never any market data, is written to the file named by ``IKARS_LIVE_SUMMARY`` if set.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.models import Market, Trade
from ikars_core.rest import KalshiPublicClient

pytestmark = pytest.mark.integration

LINES: list[str] = []


@pytest.fixture(scope="module", autouse=True)
def summary() -> Iterator[None]:
    yield
    target = os.environ.get("IKARS_LIVE_SUMMARY")
    if target:
        Path(target).write_text("\n".join(LINES) + "\n", encoding="utf-8")


@pytest.fixture
async def data() -> AsyncIterator[MarketData]:
    async with KalshiPublicClient(KalshiConfig(environment=Environment.PRODUCTION)) as client:
        yield MarketData(client)
        LINES.append(
            f"requests so far: {client.stats.requests}, retries {client.stats.retries}, "
            f"429s {client.stats.rate_limited}"
        )


async def test_the_exchange_endpoints(data: MarketData) -> None:
    status = await data.exchange_status()
    schedule = await data.exchange_schedule()
    cutoff = await data.historical_cutoff()
    LINES.append(
        f"exchange: {len(status.shards)} shards; schedule rows {len(schedule.standard_hours)}"
    )
    LINES.append(
        f"cutoff: markets and trades settled before the same day: "
        f"{cutoff.market_settled_ts == cutoff.trades_created_ts}"
    )
    assert status.shards
    assert schedule.standard_hours
    assert cutoff.market_settled_ts.tzinfo is not None


async def test_every_series_and_its_fee_history_parse(data: MarketData) -> None:
    series = await data.series_list()
    changes = await data.series_fee_changes()
    known = {"quadratic", "quadratic_with_maker_fees", "quadratic_with_combo_maker_fees"}
    unknown = {s.fee_type for s in series} - known
    LINES.append(
        f"series: {len(series)} parsed; categories {len({s.category for s in series})}; "
        f"fee types outside the modelled family: {sorted(map(str, unknown))}"
    )
    LINES.append(f"fee changes: {len(changes)} parsed")
    assert len(series) > 10_000
    assert all(s.fee_multiplier_e6 is not None for s in series)
    assert changes


async def test_historical_markets_trades_and_candles(data: MarketData) -> None:
    markets: list[Market] = []
    async for market_page in data.historical_markets(mve_filter="exclude"):
        markets += market_page.items
        if len(markets) >= 2000:
            break
    settled = [m for m in markets if m.result in {"yes", "no"}]
    LINES.append(
        f"historical markets: {len(markets)} parsed; {len(settled)} with a yes/no result; "
        f"with rules text {sum(1 for m in markets if m.rules_primary)}; "
        f"with a settlement time {sum(1 for m in markets if m.settlement_ts)}"
    )
    assert len(markets) >= 1000
    assert settled

    trades: list[Trade] = []
    async for trade_page in data.historical_trades():
        trades = trade_page.items
        break
    LINES.append(f"historical trades: {len(trades)} parsed (yes + no = $1 held for every one)")
    assert len(trades) >= 100
    newest_first = all(
        a.created_time >= b.created_time for a, b in zip(trades, trades[1:], strict=False)
    )
    LINES.append(f"historical trades newest first within the page: {newest_first}")

    market = next(m for m in settled if m.settlement_ts and m.open_time)
    start = int(market.open_time.timestamp())  # type: ignore[union-attr]
    end = int(market.settlement_ts.timestamp())  # type: ignore[union-attr]
    candles = await data.historical_candlesticks(
        market.ticker, start_ts=start, end_ts=end, period_interval=60
    )
    LINES.append(f"historical candles (60 min) for one settled market: {len(candles)} parsed")
    assert isinstance(candles, list)


async def test_live_markets_trades_events_and_candles(data: MarketData) -> None:
    cutoff = await data.historical_cutoff()
    since = int(cutoff.market_settled_ts.timestamp())
    markets: list[Market] = []
    async for market_page in data.markets(
        status="settled", mve_filter="exclude", min_settled_ts=since, limit=200
    ):
        markets = market_page.items
        break
    LINES.append(f"live settled markets after the cutoff: {len(markets)} parsed (one page of 200)")
    assert markets
    assert all(m.result is not None for m in markets if m.status == "finalized")

    trades: list[Trade] = []
    async for trade_page in data.trades(limit=200):
        trades = trade_page.items
        break
    LINES.append(f"live trades: {len(trades)} parsed")
    assert trades

    market = markets[0]
    assert market.event_ticker
    event = await data.event(market.event_ticker)
    series = await data.series(event.series_ticker or "")
    prefix_matches = market.event_ticker.split("-")[0] == event.series_ticker
    LINES.append(
        f"event -> series ticker equals the event ticker's prefix: {prefix_matches}; "
        f"event category present {bool(event.category)}; "
        f"series category present {bool(series.category)}"
    )
    assert event.series_ticker
    assert series.fee_multiplier_e6 is not None

    one = await data.market(market.ticker)
    assert one.ticker == market.ticker
    if market.open_time and market.settlement_ts:
        candles = await data.candlesticks(
            event.series_ticker,
            market.ticker,
            start_ts=int(market.open_time.timestamp()),
            end_ts=int(market.settlement_ts.timestamp()),
            period_interval=60,
        )
        LINES.append(f"live candles (60 min) for one settled market: {len(candles)} parsed")
