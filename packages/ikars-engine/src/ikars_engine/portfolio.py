"""The portfolio (PLAN D19 G1): open positions from the ledger, marked to market after exit costs.

**Lots** are first in, first out (owner, 2026-10-27; the tax package's default too). A fill that
opens contracts adds a lot costing what was paid plus its share of the fill's fee (the fee splits
between the closed and the opened contracts in proportion, the remainder to the opened part, so no
millionth is lost); a fill that closes contracts takes them from the oldest lots, each lot's cost
reduced in proportion (exactly: the remainder stays with the lot). A settlement empties the market.
The same pass feeds the import's :class:`~ikars_engine.ledger_import.Book`, so the portfolio's cash
is the reconciliation's.

**Marked to market** at the price a position could be sold for now: a YES position at the YES bid, a
NO position at the NO bid (one dollar less the YES ask). No bid (a YES bid of 0, or a YES ask of 0:
nothing offered) means nothing to sell into: worth nothing, no fee. The exit fee is the taker fee at
the bid, rounded up to the cent as Kalshi charges it. **Unrealised P&L** is that exit value less the
fee less the lots' cost. A market with no quote, or whose fee cannot be modelled, is left unpriced,
never guessed.

**Exposure** is the cost of the open lots (what is at risk), by event, series and category. All
money is in dollar millionths.
"""

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ikars_core.fees import FeeSchedule, round_up
from ikars_core.fixedpoint import CountE2, DollarsE6

from ikars_engine.ledger import Ledger
from ikars_engine.ledger_import import Book, value_e6

CENT_E6 = 10_000
DOLLAR_E6 = 1_000_000
Quote = tuple[int | None, int | None]  # (YES bid, YES ask)


@dataclass(slots=True)
class Lot:
    count_e2: int
    cost_e6: int


@dataclass(slots=True)
class Holding:
    ticker: str
    side: str  # "yes" or "no"
    lots: list[Lot] = field(default_factory=list)
    context: dict[str, str] = field(default_factory=dict)

    @property
    def count_e2(self) -> int:
        return sum(lot.count_e2 for lot in self.lots)

    @property
    def cost_e6(self) -> int:
        return sum(lot.cost_e6 for lot in self.lots)


@dataclass(frozen=True, slots=True)
class Held:
    """The open markets' lots and the cash, from one pass over the ledger."""

    lots: dict[str, Holding]
    cash_e6: int


@dataclass(frozen=True, slots=True)
class Position:
    ticker: str
    side: str
    count_e2: int
    cost_e6: int
    event_ticker: str
    series_ticker: str
    category: str
    title: str
    exit_price_e6: int | None  # the bid it would sell at
    exit_fee_e6: int | None
    unrealised_e6: int | None  # after the exit fee; None: not priced


@dataclass(frozen=True, slots=True)
class Portfolio:
    cash_e6: int
    positions: list[Position]

    @property
    def cost_e6(self) -> int:
        return sum(p.cost_e6 for p in self.positions)

    @property
    def unrealised_e6(self) -> int | None:
        """The total, or None when any position is unpriced (a partial total would mislead)."""
        values = [p.unrealised_e6 for p in self.positions]
        return None if any(v is None for v in values) else sum(v for v in values if v is not None)

    def exposure(self, by: str) -> dict[str, int]:
        """The open cost by ``event_ticker``, ``series_ticker`` or ``category``."""
        totals: dict[str, int] = defaultdict(int)
        for position in self.positions:
            totals[getattr(position, by) or "?"] += position.cost_e6
        return dict(sorted(totals.items()))


def holdings(ledger: Ledger) -> Held:
    """FIFO lots of every open market, and the cash the ledger explains (one pass)."""
    held: dict[str, Holding] = {}
    book = Book()
    for body in ledger.records():
        ticker, fields = body.get("ticker"), body.get("fields", {})
        book.apply(body["kind"], ticker, fields, body.get("cash_e6"))
        if not ticker:
            continue
        if body["kind"] == "settlement":
            held.pop(ticker, None)
        elif body["kind"] == "fill":
            _fill(held, ticker, fields, body.get("context") or {})
    return Held({t: h for t, h in held.items() if h.count_e2}, book.cash_e6)


def _fill(
    held: dict[str, Holding], ticker: str, fields: Mapping[str, Any], context: Mapping[str, str]
) -> None:
    side = str(fields["outcome_side"])
    count, closed, opened = (int(fields[k]) for k in ("count_e2", "closed_e2", "opened_e2"))
    fee = int(fields["fee_e6"])
    fee_closed = fee * closed // count if count else 0
    holding = held.get(ticker)
    if closed and holding is not None:
        _take(holding, closed)
    if opened:
        price = int(fields["yes_price_e6" if side == "yes" else "no_price_e6"])
        if holding is None or not holding.count_e2:
            holding = held[ticker] = Holding(ticker, side, context=dict(context))
        holding.lots.append(Lot(opened, value_e6(price, opened) + fee - fee_closed))


def _take(holding: Holding, count_e2: int) -> None:
    """Close ``count_e2`` contracts from the oldest lots."""
    need = count_e2
    while need and holding.lots:
        lot = holding.lots[0]
        take = min(need, lot.count_e2)
        share = lot.cost_e6 * take // lot.count_e2
        lot.count_e2 -= take
        lot.cost_e6 -= share
        need -= take
        if not lot.count_e2:
            holding.lots.pop(0)


def exit_price(side: str, quote: Quote) -> int | None:
    """The bid a position would sell at: 0 when nothing is bid, None when unknown."""
    bid, ask = quote
    if side == "yes":
        return bid
    if ask is None:
        return None
    return 0 if ask == 0 else DOLLAR_E6 - ask  # no YES offered: no NO bid


def portfolio(
    quotes: Mapping[str, Quote],
    fees: Callable[[str], FeeSchedule | None],
    category: Callable[[str], str | None],
    held: Held,
) -> Portfolio:
    """The portfolio now, priced from ``quotes``: ``fees`` and ``category`` take a series."""
    positions = []
    for ticker, holding in sorted(held.lots.items()):
        series = holding.context.get("series_ticker", "")
        price = exit_price(holding.side, quotes.get(ticker, (None, None)))
        schedule = fees(series)
        count, cost = holding.count_e2, holding.cost_e6
        fee = unrealised = None
        if price == 0:  # nothing to sell into: worth nothing, no fee
            fee, unrealised = 0, -cost
        elif price is not None and schedule is not None:
            fee = round_up(schedule.taker_fee_e6(CountE2(count), DollarsE6(price)), CENT_E6)
            unrealised = value_e6(price, count) - fee - cost
        positions.append(
            Position(
                ticker=ticker,
                side=holding.side,
                count_e2=count,
                cost_e6=cost,
                event_ticker=holding.context.get("event_ticker", ""),
                series_ticker=series,
                category=category(series) or "",
                title=holding.context.get("title", ""),
                exit_price_e6=price,
                exit_fee_e6=fee,
                unrealised_e6=unrealised,
            )
        )
    return Portfolio(held.cash_e6, positions)
