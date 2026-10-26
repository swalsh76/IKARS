"""One market's order book, kept from an ``orderbook_snapshot`` and the ``orderbook_delta`` that
follow it. Pure and synchronous: the feed, the subscription and any resync are the engine's job
(M1.4b); this only applies messages and says when the book can no longer be trusted.

Each side holds that side's **bids**. A YES ask is $1 minus the best NO bid, and a NO ask is $1
minus the best YES bid. Prices are dollar millionths and sizes contract hundredths, as everywhere.

A delta is applied only after a snapshot. When both carry a ``clock``, a delta at or below the
snapshot's clock is already in it and is skipped (several deltas may share one clock). Production
frames had no clock on 2026-10-26; without one, order rests on the subscription's ``seq``, which the
feed checks for gaps (a gap there must also be met with a new snapshot). A level
that would go negative means a message was missed: the book is then **broken** until the next
snapshot, and the caller should ask for one (``update_subscription`` with ``get_snapshot``). A
crossed book (the best YES bid plus the best NO bid above $1) is reported, not corrected.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from ikars_core.fixedpoint import E6
from ikars_core.ws_models import Level, OrderbookDeltaMsg, OrderbookSnapshotMsg


class Applied(StrEnum):
    APPLIED = "applied"
    STALE = "stale"  # already in the snapshot (clock at or below the snapshot's): ignored
    NO_SNAPSHOT = "no_snapshot"  # a delta before any snapshot: ignored; ask for a snapshot
    BROKEN = "broken"  # a level would go negative: the book is untrusted until a snapshot
    WRONG_MARKET = "wrong_market"


@dataclass(slots=True)
class OrderBook:
    market_ticker: str
    yes: dict[int, int] = field(default_factory=dict)  # price_e6 -> count_e2
    no: dict[int, int] = field(default_factory=dict)
    snapshot_clock: int | None = None
    clock: int | None = None  # the newest clock applied, when messages carry one
    has_snapshot: bool = False
    broken: bool = False

    @property
    def ready(self) -> bool:
        """A snapshot has been applied and nothing since has broken the book."""
        return self.has_snapshot and not self.broken

    def apply_snapshot(self, msg: OrderbookSnapshotMsg) -> Applied:
        if msg.market_ticker != self.market_ticker:
            return Applied.WRONG_MARKET
        self.yes = _side(msg.yes)
        self.no = _side(msg.no)
        self.snapshot_clock = self.clock = msg.clock
        self.has_snapshot = True
        self.broken = False
        return Applied.APPLIED

    def apply_delta(self, msg: OrderbookDeltaMsg) -> Applied:
        if msg.market_ticker != self.market_ticker:
            return Applied.WRONG_MARKET
        if not self.has_snapshot:
            return Applied.NO_SNAPSHOT
        if self.broken:
            return Applied.BROKEN
        before = self.snapshot_clock
        if msg.clock is not None and before is not None and msg.clock <= before:
            return Applied.STALE
        levels = self.yes if msg.side == "yes" else self.no
        count = levels.get(msg.price_e6, 0) + msg.delta_e2
        if count < 0:
            self.broken = True
            return Applied.BROKEN
        if count == 0:
            levels.pop(msg.price_e6, None)
        else:
            levels[msg.price_e6] = count
        if msg.clock is not None:
            self.clock = max(self.clock or 0, msg.clock)
        return Applied.APPLIED

    def bids(self, side: str) -> list[Level]:
        """A side's bids, best (highest price) first."""
        levels = self.yes if side == "yes" else self.no
        return sorted(levels.items(), reverse=True)

    def best_bid(self, side: str) -> Level | None:
        levels = self.yes if side == "yes" else self.no
        if not levels:
            return None
        price = max(levels)
        return price, levels[price]

    def best_ask(self, side: str) -> Level | None:
        """The best ask on ``side``: $1 minus the other side's best bid, at that bid's size."""
        other = self.best_bid("no" if side == "yes" else "yes")
        return None if other is None else (E6 - other[0], other[1])

    @property
    def crossed(self) -> bool:
        yes, no = self.best_bid("yes"), self.best_bid("no")
        return yes is not None and no is not None and yes[0] + no[0] > E6


def _side(levels: tuple[Level, ...]) -> dict[int, int]:
    side: dict[int, int] = {}
    for price, count in levels:
        if count < 0:
            raise ValueError(f"a snapshot level has a negative size: {price}, {count}")
        if count:
            side[price] = side.get(price, 0) + count
    return side
