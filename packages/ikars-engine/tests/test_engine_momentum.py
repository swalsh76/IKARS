"""M1.12b: the momentum signal, its registration, its filters, and P&L after fees (made-up markets)."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from ikars_core.fees import fee_schedule
from ikars_engine import pnl
from ikars_engine.clocks import VirtualClock
from ikars_engine.journal import Entry, Mark, Recommendation
from ikars_engine.kalshi.discovery import MarketInfo
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import PluginContext
from ikars_engine.signals.momentum import RULE, VERSION, Momentum

S = 1_000_000_000
T0 = 1_767_225_600 * S  # 2026-01-01 00:00 UTC
TICKER = "KXDEMO-26JAN01-T50"
FEES = fee_schedule("quadratic", 1_000_000)


def info(status: str = "active", closes_in_s: int = 86_400) -> MarketInfo:
    closes = datetime.fromtimestamp(T0 // S + closes_in_s, UTC)
    return MarketInfo(ticker=TICKER, event_ticker="KXDEMO-26JAN01", series_ticker="KXDEMO",
                      category="Demo", title="made up", status=status, close_time=closes,
                      result=None, volume_24h_e2=100_000)  # fmt: skip


class Setup:
    def __init__(self, market: MarketInfo | None = None, fees: Any = FEES) -> None:
        self.plugin = Momentum()
        self.emitted: list[Item] = []

        async def emit(item: Item) -> None:
            self.emitted.append(item)

        found = info() if market is None else market
        services = {"market": lambda t: found if t == TICKER else None, "fees": lambda s: fees}
        self.ctx = PluginContext(self.plugin.spec, VirtualClock(T0), {}, emit, 1.0, services)

    async def ticker(self, at_s: int, bid: int, ask: int, size: int = 5_000) -> None:
        payload = {"market_ticker": TICKER, "yes_bid_e6": bid, "yes_ask_e6": ask,
                   "yes_bid_size_e2": size, "yes_ask_size_e2": size}  # fmt: skip
        await self.plugin.handle(Item(ref=Ref("ticker", TICKER), source="kalshi",
                                      received_ns=T0 + at_s * S, payload=payload), self.ctx)  # fmt: skip

    async def trade(self, at_s: int, count_e2: int) -> None:
        await self.plugin.handle(Item(ref=Ref("trade", TICKER), source="kalshi",
                                      received_ns=T0 + at_s * S,
                                      payload={"count_e2": count_e2}), self.ctx)  # fmt: skip

    async def rise(self, *, to_bid: int = 440_000, to_ask: int = 460_000, traded: int = 6_000,
                   at_s: int = 200, size: int = 5_000) -> None:  # fmt: skip
        await self.ticker(0, 400_000, 420_000)
        await self.trade(100, traded)
        await self.ticker(at_s, to_bid, to_ask, size)


def test_the_rule_is_registered_by_its_hash() -> None:
    assert VERSION == hashlib.sha256(json.dumps(RULE, sort_keys=True).encode()).hexdigest()[:12]
    changed = {**RULE, "move_e6": 50_000}
    assert hashlib.sha256(json.dumps(changed, sort_keys=True).encode()).hexdigest()[:12] != VERSION
    assert Momentum.spec.version == VERSION


async def test_a_rise_recommends_yes_with_its_p_and_l_after_fees() -> None:
    setup = Setup()
    await setup.rise()
    [made] = setup.emitted
    rec = Recommendation(**{k: v for k, v in made.payload.items() if k != "inputs"},
                         inputs=[Ref(*r) for r in made.payload["inputs"]])  # fmt: skip
    assert made.ref.kind == "recommendation" and made.ref.id.startswith(f"momentum-{TICKER}-")
    assert (rec.side, rec.entry_price_e6, rec.count_e2) == ("yes", 460_000, 1_000)
    assert rec.version == VERSION and rec.horizons_s == [1800, 3600]
    assert rec.expires_ns == T0 + 200 * S + 1800 * S
    # bought at 46c, sold at the target 50c, 10 contracts: $0.40, less 18c of fees each way
    assert rec.pnl_e6 == 40_000
    assert "up 4c in 5 min on 60 traded" in rec.summary
    assert rec.inputs[0] == Ref("market", TICKER)


async def test_a_fall_recommends_no_at_the_no_ask() -> None:
    setup = Setup()
    await setup.rise(to_bid=360_000, to_ask=380_000)  # the mid falls 4c
    [made] = setup.emitted
    assert made.payload["side"] == "no" and made.payload["entry_price_e6"] == 1_000_000 - 360_000


@pytest.mark.parametrize(
    ("kw", "market", "fees"),
    [
        ({"to_bid": 430_000, "to_ask": 450_000}, None, FEES),  # moved 3c: not enough
        ({"traded": 4_000}, None, FEES),  # 40 contracts traded: too few
        ({"to_bid": 430_000, "to_ask": 470_000}, None, FEES),  # a 4c spread: too wide
        ({"size": 500}, None, FEES),  # the ask holds 5: less than the 10
        ({"at_s": 400}, None, FEES),  # the first mid left the 5 minutes: no move inside them
        ({}, info(status="closed"), FEES),  # not trading
        ({}, info(closes_in_s=1_000), FEES),  # closes before the recommendation would expire
        ({}, None, None),  # a fee that cannot be modelled
    ],
)
async def test_a_move_that_fails_a_filter_recommends_nothing(
    kw: dict[str, int], market: MarketInfo | None, fees: Any
) -> None:
    setup = Setup(market, fees)
    await setup.rise(**kw)
    assert setup.emitted == []


async def test_prices_outside_10_to_90_cents_are_left_alone_and_one_at_a_time() -> None:
    setup = Setup()
    await setup.ticker(0, 920_000, 940_000)
    await setup.trade(10, 9_000)
    await setup.ticker(100, 960_000, 980_000)  # up 4c, but at 97c
    assert setup.emitted == []
    again = Setup()
    await again.rise()
    await again.ticker(300, 480_000, 500_000)  # it carries on: the first is still valid
    assert len(again.emitted) == 1


def test_a_round_trip_counts_both_fees_and_a_no_contract_s_prices() -> None:
    assert pnl.round_trip_e6(FEES, 1_000, 460_000, 500_000) == 40_000
    assert (
        pnl.round_trip_e6(FEES, 1_000, 460_000, 0) == -4_600_000 - 180_000
    )  # nothing to sell into
    assert pnl.buy_price_e6("no", 360_000, 380_000) == 640_000
    assert pnl.sell_price_e6("no", 360_000, 380_000) == 620_000
    assert pnl.buy_price_e6("yes", None, 380_000) == 380_000
    assert pnl.sell_price_e6("no", 360_000, None) is None


def test_each_mark_is_scored_after_fees() -> None:
    from ikars_engine.api import recommendation_view

    rec = Recommendation(ticker=TICKER, side="yes", entry_price_e6=460_000, count_e2=1_000,
                         expires_ns=T0 + 1800 * S, summary="s", strategy="momentum",
                         version=VERSION, horizons_s=[1800, 3600])  # fmt: skip
    entry = Entry("r1", T0, "expired", None, "momentum", rec,
                  [Mark(1800, T0 + 1800 * S, 500_000, 510_000), Mark(3600, T0 + 3600 * S, None, 1)])  # fmt: skip
    view = recommendation_view(entry, lambda ticker: FEES)
    assert [m.score_e6 for m in view.marks] == [40_000, None]  # sold at 50c; then no bid
    assert recommendation_view(entry).marks[0].score_e6 is None  # no fee model: no score
