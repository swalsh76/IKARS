"""Momentum on Kalshi's own feed (M1.12b; PLAN D13, D21-2): the first signal, end to end.

**The rule, pre-registered (owner, 2026-10-27).** Continuation: when a market's YES mid has moved
4 cents or more within 5 minutes, on at least 50 contracts traded in those 5 minutes, recommend
buying the side it moved toward (YES when it rose, NO when it fell), valid for 30 minutes and marked
at 30 and 60 minutes. Its parameters are hashed into ``VERSION`` (G4): a change is a new version
with its own record, never a quiet edit of an old one.

**Tradability (D20 E)** before any recommendation: the market is active and closes more than the
validity away; the spread is 3 cents or less; the mid is from 10 to 90 cents; the best ask of the
side holds the fixed 10 contracts; the series' fee can be modelled. One recommendation per market at
a time (a newer one waits until the older's validity ends).

**P&L after fees (D7)** is at the rule's target: sold after the move carries on by its own size,
both legs' taker fees counted. The journal marks the market at the horizons; the score after fees
comes from those marks. Recommend-only: IKARS sends no order (D6).
"""

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import msgspec
from ikars_core.fixedpoint import E6

from ikars_engine.journal import Recommendation
from ikars_engine.kalshi.discovery import OPEN_STATUSES
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import PluginBase, PluginContext, PluginKind, PluginSpec
from ikars_engine.pnl import buy_price_e6, round_trip_e6

S = 1_000_000_000
RULE: dict[str, Any] = {
    "name": "momentum",
    "kind": "continuation",
    "move_e6": 40_000,  # 4 cents
    "window_s": 300,  # within 5 minutes
    "min_traded_e2": 5_000,  # on at least 50 contracts
    "max_spread_e6": 30_000,  # 3 cents
    "low_e6": 100_000,  # the mid from 10 cents
    "high_e6": 900_000,  # to 90 cents
    "valid_s": 1_800,  # 30 minutes
    "horizons_s": [1_800, 3_600],
    "count_e2": 1_000,  # 10 contracts (paper only)
    "target": "the move again",  # the P&L's exit: the move carries on by its own size
}
# the registration (G4): the parameters, canonically written, hashed
VERSION = hashlib.sha256(json.dumps(RULE, sort_keys=True).encode()).hexdigest()[:12]
IDLE_NS = 3_600 * S  # a market with nothing for this long is forgotten
FORGET_EVERY = 10_000  # items between two looks for idle markets


@dataclass
class Market:
    """What the rule keeps of one market: its recent mids and trades, inside the window."""

    mids: deque[tuple[int, int]] = field(default_factory=deque)  # (received_ns, mid_e6)
    trades: deque[tuple[int, int]] = field(default_factory=deque)  # (received_ns, count_e2)
    traded_e2: int = 0
    quiet_until_ns: int = 0  # one recommendation per market at a time
    last_ns: int = 0

    def prune(self, now_ns: int) -> None:
        """Keep the window, and the last mid before it: the price at the window's start (Kalshi
        sends a ticker only on a change, so a quiet market's price is its last one; review 4)."""
        oldest = now_ns - RULE["window_s"] * S
        while len(self.mids) > 1 and self.mids[1][0] <= oldest:
            self.mids.popleft()
        while self.trades and self.trades[0][0] < oldest:
            self.traded_e2 -= self.trades.popleft()[1]


def _nothing(key: str) -> None:
    """A service the host does not offer (a test, a replay without the index): nothing known."""
    return None


def _never(strategy: str, ticker: str, now_ns: int) -> bool:
    """No journal to ask (a test, a replay): nothing is active."""
    return False


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


class Momentum(PluginBase):
    spec = PluginSpec(
        name="momentum",
        kind=PluginKind.SIGNAL,
        version=VERSION,
        consumes=("ticker", "trade"),
        produces=("recommendation",),
    )

    def __init__(self) -> None:
        self.markets: dict[str, Market] = {}
        self.handled = 0

    def forget_idle(self, now_ns: int) -> None:
        """Markets with nothing inside the window for an hour are dropped (review 6)."""
        idle = now_ns - IDLE_NS
        for ticker in [t for t, m in self.markets.items() if m.last_ns < idle]:
            del self.markets[ticker]

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        ticker = item.ref.id
        now = item.received_ns
        market = self.markets.get(ticker)
        if market is None:  # made only when new, not on every item (review 6)
            market = self.markets[ticker] = Market()
        market.last_ns = now
        self.handled += 1
        if self.handled % FORGET_EVERY == 0:
            self.forget_idle(now)
        if item.ref.kind == "trade":
            count = _int(item.payload.get("count_e2"))
            if count is not None and count > 0:
                market.trades.append((now, count))
                market.traded_e2 += count
            market.prune(now)
            return
        bid, ask = _int(item.payload.get("yes_bid_e6")), _int(item.payload.get("yes_ask_e6"))
        if bid is None or ask is None or ask <= bid:
            return
        mid = (bid + ask) // 2
        market.mids.append((now, mid))
        market.prune(now)
        made = self.recommend(ctx, item, market, bid, ask, mid)
        if made is not None:
            market.quiet_until_ns = now + RULE["valid_s"] * S
            await ctx.emit(made)

    def recommend(
        self, ctx: PluginContext, item: Item, market: Market, bid: int, ask: int, mid: int
    ) -> Item | None:
        """The recommendation this ticker update makes, or None (the rule and its filters)."""
        now, ticker = item.received_ns, item.ref.id
        if now < market.quiet_until_ns or len(market.mids) < 2:
            return None
        move = mid - market.mids[0][1]
        if abs(move) < RULE["move_e6"] or market.traded_e2 < RULE["min_traded_e2"]:
            return None
        if ask - bid > RULE["max_spread_e6"] or not RULE["low_e6"] <= mid <= RULE["high_e6"]:
            return None
        side: Literal["yes", "no"] = "yes" if move > 0 else "no"
        # the side's best ask must hold the size (a NO ask is the YES bid)
        size = item.payload.get("yes_ask_size_e2" if side == "yes" else "yes_bid_size_e2")
        if (_int(size) or 0) < RULE["count_e2"]:
            return None
        info = ctx.services.get("market", _nothing)(ticker)
        if info is None or info.status not in OPEN_STATUSES:  # "active" or "open" (review 3)
            return None
        if ctx.services.get("active", _never)(RULE["name"], ticker, now):
            return None  # still valid in the journal: after a restart too (review 5)
        if info.close_time is not None:
            closes_ns = int(info.close_time.timestamp()) * S
            if closes_ns <= now + RULE["valid_s"] * S:
                return None
        fees = ctx.services.get("fees", _nothing)(info.series_ticker)
        entry = buy_price_e6(side, bid, ask)
        if fees is None or entry is None:
            return None  # a fee that cannot be modelled: no P&L, so no recommendation
        target = min(E6 - 10_000, entry + abs(move))
        pnl = round_trip_e6(fees, RULE["count_e2"], entry, target)
        moved = f"{'up' if move > 0 else 'down'} {abs(move) / 10_000:.0f}c in 5 min"
        rec = Recommendation(
            ticker=ticker, event_ticker=info.event_ticker, side=side, entry_price_e6=entry,
            count_e2=RULE["count_e2"], expires_ns=now + RULE["valid_s"] * S,
            summary=f"{moved} on {market.traded_e2 // 100} traded: buy {side.upper()}",
            strategy=RULE["name"], version=VERSION, horizons_s=list(RULE["horizons_s"]),
            inputs=[Ref("market", ticker), item.ref], pnl_e6=pnl,
        )  # fmt: skip
        when = datetime.fromtimestamp(now // S, UTC).strftime("%Y%m%dT%H%M%SZ")
        return Item(ref=Ref("recommendation", f"momentum-{ticker}-{when}"), source="momentum",
                    received_ns=now, payload=msgspec.to_builtins(rec))  # fmt: skip
