"""The owner's Kalshi account into the ledger, and the ledger against Kalshi (PLAN D18).

**Import.** The first import reads the account's whole history (fills live and historical,
settlements, deposits, withdrawals, transfers between the account's exchange instances); later ones
read fills (live and archived: after a long outage new fills may already be past Kalshi's archive
cutoff) from a day before the newest fill in, settlements from a day before the newest settlement
in, and every transfer (there are few). New records are appended in Kalshi's time order; one
already in is skipped, so an import can run any number of times. Manual trades are fills like any
other. A market the live index does not describe is looked up once, so its records carry a title.

**Cash.** Each record carries its effect on the account's cash (``cash_e6``), computed exactly in
dollar millionths. Kalshi nets YES and NO in a market, so a fill that adds YES exposure while NO is
held **closes** NO contracts (the account receives the NO price for each) before it **opens** YES
contracts (paying the YES price), and the other way round; the fee is paid on top. A settlement pays
its revenue and closes the market's position (its ``fee_cost`` repeats fees already paid on the
fills, so it is kept as a field, not charged again: checked live against Kalshi's realised P&L); a
deposit adds its amount less its fee; a withdrawal takes its amount and fee from when it is
requested, and a failed or returned one is reversed by a new record. A transfer counts only when it
moves money into or out of the event-contract instance.

**Reconciliation.** After each import the cash the ledger explains is compared with Kalshi's
balance, and its positions with Kalshi's. A difference is never fixed: it is written to the ledger
as a reconciliation record (only when the result changes) and shown in health as a count. Kalshi
reports no interest (M1.2), so interest arrives as an unexplained difference until the owner labels
it with ``ledger annotate``.

**Archived settlements.** Kalshi moves settled positions to an archive and then no longer lists
their settlement records, and it has no archived equivalent (its documentation says so). For a
market the ledger still holds but Kalshi shows flat and archived, the settlement is **derived** from
the market's result (contracts held, $1 each on the winning side) and appended only when the
ledger's realised result for that market then equals Kalshi's archived realised P&L exactly; it is
marked ``derived`` and keeps Kalshi's archived row and the market's result as its evidence. A market
that does not check out is left as a difference. A listed settlement for a market already settled
this way is skipped, so a payout is never counted twice.

Each import replays the ledger once (a :class:`Book`) and keeps it current in memory. Logs carry
counts only; amounts, tickers and ids stay in the ledger.
"""

import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ikars_core.account import (
    Account,
    Fill,
    InstanceTransfer,
    MarketPosition,
    Positions,
    Settlement,
    Transfer,
)
from ikars_core.endpoints import MarketData, TypedPage
from ikars_core.markets import series_ticker_of
from ikars_core.models import Market
from ikars_core.rest import KalshiAPIError

from ikars_engine.clocks import EventClock
from ikars_engine.ledger import Entry, Ledger, LedgerError

log = logging.getLogger("ikars_engine.ledger")
NS = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICROSECOND = timedelta(microseconds=1)
OVERLAP_S = 86_400  # each later import re-reads a day of fills and settlements
FIRST_DONE = "import.first_done"
ARCHIVED = "settlement_archived"  # the source of a settlement derived from Kalshi's archive
EVENT_CONTRACT = "event_contract"
DEPOSITED = frozenset({"applied"})
WITHDRAWN = frozenset({"pending", "applied"})  # the money has left the balance
UNDONE = frozenset({"failed", "returned"})
Describe = Callable[[str], Mapping[str, str] | None]
Found = tuple[int, int, Any, Mapping[str, Any]]  # (time, order within a time, model, raw JSON)


async def _gather[M](
    pages: AsyncIterator[TypedPage[M]], order: int, time_of: Callable[[M], int]
) -> list[Found]:
    """Every row of every page, with its time and its raw JSON."""
    found: list[Found] = []
    async for page in pages:
        found += [(time_of(m), order, m, r) for m, r in zip(page.items, page.raw, strict=True)]
    return found


def ns_of(moment: datetime) -> int:
    return (moment - EPOCH) // MICROSECOND * 1_000


def value_e6(price_e6: int, count_e2: int) -> int:
    """The dollars (in millionths) of ``count_e2`` hundredths of a contract at ``price_e6``; a
    result finer than a millionth is refused, never rounded."""
    product = price_e6 * count_e2
    if product % 100:
        raise LedgerError("an amount finer than a millionth of a dollar")
    return product // 100


def fill_cash(fill: Fill, position_e2: int) -> tuple[int, int, int]:
    """(cash, contracts closed, contracts opened) for ``fill``, given the market's net position
    before it (hundredths; negative: NO)."""
    count = fill.count_e2
    if fill.outcome_side == "yes":
        closed = min(count, max(0, -position_e2))
        cash = value_e6(fill.no_price_e6, closed) - value_e6(fill.yes_price_e6, count - closed)
    else:
        closed = min(count, max(0, position_e2))
        cash = value_e6(fill.yes_price_e6, closed) - value_e6(fill.no_price_e6, count - closed)
    return cash - fill.fee_e6, closed, count - closed


ARCHIVE_RECHECK_NS = 3_600 * NS  # an archived market that did not check out


@dataclass(frozen=True, slots=True)
class Reconciliation:
    balance_e6: int
    explained_e6: int
    positions: dict[str, tuple[int, int]]  # ticker -> (ledger, Kalshi), only where they differ
    reserved_e6: int = 0  # the cost of resting buy orders, read only when the balance falls short

    @property
    def difference_e6(self) -> int:
        return self.balance_e6 - self.explained_e6

    @property
    def exact(self) -> bool:
        # Kalshi's balance is what is available for trading: a resting buy holds its cost back
        # (M1.R 8). Either reading is exact; the record keeps both numbers.
        return not self.positions and self.difference_e6 in (0, -self.reserved_e6)


@dataclass(slots=True)
class ImportReport:
    added: Counter[str] = field(default_factory=Counter)
    reconciliation: Reconciliation | None = None


@dataclass(slots=True)
class Book:
    """What the ledger says, from one replay: each market's net position (hundredths; negative:
    NO), the cash it explains, each market's trading cash before fees, and the markets settled."""

    positions: dict[str, int] = field(default_factory=dict)
    cash_e6: int = 0
    traded_e6: dict[str, int] = field(default_factory=dict)
    settled: set[str] = field(default_factory=set)
    last_fill_ns: dict[str, int] = field(default_factory=dict)  # each market's newest fill time

    def apply(
        self,
        kind: str,
        ticker: str | None,
        fields: Mapping[str, Any],
        cash: int | None,
        at_ns: int | None = None,
    ) -> None:
        if cash is not None:
            self.cash_e6 += cash
        if not ticker:
            return
        if kind == "fill":
            if at_ns is not None:
                self.last_fill_ns[ticker] = max(self.last_fill_ns.get(ticker, at_ns), at_ns)
            self.positions[ticker] = self.positions.get(ticker, 0) + int(fields["delta_e2"])
            before_fees = int(cash or 0) + int(fields["fee_e6"])
            self.traded_e6[ticker] = self.traded_e6.get(ticker, 0) + before_fees
        elif kind == "settlement":
            self.positions[ticker] = 0
            self.settled.add(ticker)

    def add(self, entry: Entry) -> None:
        self.apply(entry.kind, entry.ticker, entry.fields, entry.cash_e6, entry.at_ns)

    def apply_body(self, body: Mapping[str, Any]) -> None:
        self.apply(
            body["kind"], body.get("ticker"), body.get("fields", {}), body.get("cash_e6"),
            body.get("at_ns"),
        )  # fmt: skip

    def held(self) -> dict[str, int]:
        """The markets that are not flat."""
        return {t: p for t, p in self.positions.items() if p}


def replay(ledger: Ledger) -> Book:
    book = Book()
    for body in ledger.records():
        book.apply_body(body)
    return book


class AccountImporter:
    def __init__(
        self,
        ledger: Ledger,
        account: Account,
        clock: EventClock,
        describe: Describe | None = None,
        markets: MarketData | None = None,
    ) -> None:
        self._ledger = ledger
        self._account = account
        self._clock = clock
        self._describe = describe
        self._markets = markets  # market results and titles; None: neither is looked up
        self._looked_up: dict[str, dict[str, str]] = {}
        # the book, kept current from the records added since it was built, instead of a replay of
        # the whole ledger every minute (M1.R 15)
        self._book: Book | None = None
        self._book_seq = 0
        self._in_book: set[int] = set()  # records this importer appended (already in the book)
        self._archive_checked: dict[str, int] = {}  # ticker -> when it last failed to check out

    def _current_book(self) -> Book:
        if self._book is None:
            self._book, self._book_seq, self._in_book = Book(), 0, set()
        for body in self._ledger.records(after=self._book_seq):  # annotations, other writers
            if body["seq"] not in self._in_book:
                self._book.apply_body(body)
            self._book_seq = body["seq"]
        self._in_book.clear()
        return self._book

    async def run_once(self) -> ImportReport:
        try:
            return await self._run_once()
        except BaseException:
            self._book = (
                None  # it may hold entries that never reached the ledger: rebuilt next time
            )
            raise

    async def _run_once(self) -> ImportReport:
        first = self._ledger.bookmark(FIRST_DONE) is None
        found = await self._read(first)
        found.sort(key=lambda item: (item[0], item[1]))
        await self._look_up({m.ticker for _, _, m, _ in found if isinstance(m, (Fill, Settlement))})
        book = self._current_book()
        entries: list[Entry] = []
        seen: set[tuple[str, str]] = set()
        for at_ns, _, model, raw in found:
            entry = self._entry(at_ns, model, raw, book)
            if entry is None or entry.source_id is None:
                continue
            key = (entry.source, entry.source_id)
            if key in seen or self._ledger.has(*key):
                continue
            seen.add(key)
            if entry.fields.get("out_of_order"):  # said only for one added (the overlap re-reads)
                log.warning("ledger: a fill arrived after a later one; flagged for the owner")
            book.add(entry)
            entries.append(entry)
        self._in_book.update(self._ledger.append_all(entries))  # the book already holds these
        if first:  # the history is in: never read it all again, whatever fails below
            self._ledger.set_bookmark(FIRST_DONE, str(self._clock.now_ns()))
        held = await self._account.positions()
        derived = await self._derive_archived(held, book)
        self._in_book.update(self._ledger.append_all(derived))
        for entry in derived:
            book.add(entry)
        reconciliation = await self.reconcile(held, book)
        log.info(
            "ledger import",
            extra={
                "added": len(entries) + len(derived),
                "derived": len(derived),
                "exact": reconciliation.exact,
            },
        )
        return ImportReport(Counter(e.kind for e in [*entries, *derived]), reconciliation)

    async def _read(self, first: bool) -> list[Found]:
        """Everything to consider, as (time, order within a time, model, raw JSON)."""
        found: list[Found] = []
        fills_since = None if first else self._since("fill")
        for historical in (False, True):
            pages = self._account.fills(min_ts=fills_since, historical=historical)
            found += await _gather(pages, 0, lambda f: ns_of(f.created_time))
        settled = self._account.settlements(min_ts=None if first else self._since("settlement"))
        found += await _gather(settled, 1, lambda s: ns_of(s.settled_time))
        for kind, transfers in (
            ("deposit", self._account.deposits()),
            ("withdrawal", self._account.withdrawals()),
        ):
            found += [
                (at, order, (kind, t), raw)
                for at, order, t, raw in await _gather(transfers, 2, lambda t: t.created_ts * NS)
            ]
        found += await _gather(self._account.instance_transfers(), 3, lambda t: t.created_ts * NS)
        return found

    def _since(self, source: str) -> int | None:
        """A day before the newest record from ``source`` (each its own), or None: everything."""
        latest = self._ledger.latest_at_ns(source)
        return None if latest is None else max(0, latest // NS - OVERLAP_S)

    # --- market details ----------------------------------------------------------------------

    async def _look_up(self, tickers: set[str]) -> None:
        """Titles for markets the live index does not have (settled ones, or every market for the
        command line, which has no index), once each: a record's context is never edited later."""
        if self._markets is None:
            return
        for ticker in sorted(tickers):
            if ticker in self._looked_up or (self._describe and self._describe(ticker)):
                continue
            market = await self.market_of(self._markets, ticker)
            event = (market.event_ticker or "") if market is not None else ""
            self._looked_up[ticker] = (
                {}
                if market is None
                else {
                    "title": market.title or "",
                    "event_ticker": event,
                    "series_ticker": series_ticker_of(event) or "",
                }  # fmt: skip
            )

    def _context(self, ticker: str) -> dict[str, str]:
        described = self._describe(ticker) if self._describe is not None else None
        return dict(described or self._looked_up.get(ticker) or {})

    @staticmethod
    async def market_of(markets: MarketData, ticker: str) -> Market | None:
        """The market, live or archived; None when Kalshi has neither."""
        for read in (markets.market, markets.historical_market):
            try:
                return await read(ticker)
            except KalshiAPIError as exc:
                if exc.status != 404:
                    raise
        return None

    # --- archived settlements ----------------------------------------------------------------

    async def _derive_archived(self, live: Positions, book: Book) -> list[Entry]:
        if self._markets is None:
            return []
        held_live = {p.ticker for p in live.market_positions if p.position_e2}
        now = self._clock.now_ns()
        candidates = {
            t
            for t in book.held()
            if t not in held_live
            and now - self._archive_checked.get(t, -ARCHIVE_RECHECK_NS) >= ARCHIVE_RECHECK_NS
        }  # one that did not check out is looked at again hourly, not every minute (M1.R 18)
        if not candidates:
            return []
        archived = await self._account.positions(historical=True)
        derived: list[Entry] = []
        for position in archived.market_positions:
            if position.ticker not in candidates:
                continue
            market = await self.market_of(self._markets, position.ticker)
            entry = None if market is None else self._settle(position, market, book)
            if entry is None:
                self._archive_checked[position.ticker] = now
                log.warning("ledger: an archived settlement does not check out; left a difference")
            else:
                derived.append(entry)
        return derived

    def _settle(self, position: MarketPosition, market: Market, book: Book) -> Entry | None:
        """The derived settlement, or None unless Kalshi's realised P&L confirms it exactly."""
        if market.result not in ("yes", "no"):
            return None  # a scalar or undetermined result is never guessed
        held_e2 = book.positions[position.ticker]
        wins = (market.result == "yes") == (held_e2 > 0)
        payout = value_e6(1_000_000, abs(held_e2)) if wins else 0
        if book.traded_e6.get(position.ticker, 0) + payout != position.realized_pnl_e6:
            return None
        moment = market.settlement_ts or position.last_updated_ts
        settled_at = None if market.settlement_ts is None else market.settlement_ts.isoformat()
        evidence = {
            "archived_position": position.model_dump(mode="json"),
            "market": {"ticker": market.ticker, "status": market.status, "result": market.result,
                       "settlement_ts": settled_at},
        }  # fmt: skip
        return Entry(
            kind="settlement",
            at_ns=ns_of(moment),
            source=ARCHIVED,
            source_id=position.ticker,
            ticker=position.ticker,
            fields={
                "derived": True,
                "market_result": market.result,
                "held_e2": held_e2,
                "payout_e6": payout,
                "kalshi_realized_pnl_e6": position.realized_pnl_e6,
                "kalshi_fees_paid_e6": position.fees_paid_e6,
            },
            cash_e6=payout,
            raw=evidence,
            context=self._context(position.ticker),
            note="derived: Kalshi archived this settlement; its realised P&L confirms the payout",
        )

    # --- records -----------------------------------------------------------------------------

    def _entry(self, at_ns: int, model: Any, raw: Mapping[str, Any], book: Book) -> Entry | None:
        if isinstance(model, Fill):
            entry = self._fill(at_ns, model, raw, book.positions.get(model.ticker, 0))
            if at_ns < book.last_fill_ns.get(model.ticker, at_ns):
                # listed after a later fill of the same market was recorded: it is costed against
                # the position as it is now, which may differ from the position at its own time.
                # An append-only ledger cannot re-cost the later fills; the owner labels it (M1.R 9)
                return replace(
                    entry,
                    fields={**entry.fields, "out_of_order": True},
                    note="listed after a later fill of this market: check its cash and label it",
                )
            return entry
        if isinstance(model, Settlement):
            if self._ledger.has(ARCHIVED, model.ticker):
                return None  # already settled from Kalshi's archive: never paid twice
            return self._settlement(at_ns, model, raw)
        if isinstance(model, InstanceTransfer):
            return self._instance_transfer(at_ns, model, raw)
        kind, transfer = model
        return self._transfer(at_ns, kind, transfer, raw)

    def _fill(self, at_ns: int, fill: Fill, raw: Mapping[str, Any], before: int) -> Entry:
        cash, closed, opened = fill_cash(fill, before)
        delta = fill.count_e2 if fill.outcome_side == "yes" else -fill.count_e2
        return Entry(
            kind="fill",
            at_ns=at_ns,
            source="fill",
            source_id=fill.fill_id,
            ticker=fill.ticker,
            fields={
                "order_id": fill.order_id,
                "outcome_side": fill.outcome_side,
                "book_side": fill.book_side,
                "count_e2": fill.count_e2,
                "yes_price_e6": fill.yes_price_e6,
                "no_price_e6": fill.no_price_e6,
                "fee_e6": fill.fee_e6,
                "is_taker": fill.is_taker,
                "delta_e2": delta,
                "position_before_e2": before,
                "closed_e2": closed,
                "opened_e2": opened,
            },
            cash_e6=cash,
            raw=raw,
            context=self._context(fill.ticker),
        )

    def _settlement(self, at_ns: int, s: Settlement, raw: Mapping[str, Any]) -> Entry:
        return Entry(
            kind="settlement",
            at_ns=at_ns,
            source="settlement",
            source_id=f"{s.ticker}@{at_ns}",  # Kalshi gives a settlement no id of its own
            ticker=s.ticker,
            fields={
                "event_ticker": s.event_ticker,
                "market_result": s.market_result,
                "yes_count_e2": s.yes_count_e2,
                "yes_total_cost_e6": s.yes_total_cost_e6,
                "no_count_e2": s.no_count_e2,
                "no_total_cost_e6": s.no_total_cost_e6,
                "revenue_e6": s.revenue_e6,
                "fee_e6": s.fee_e6,
                "value_e6": s.value_e6,
            },
            cash_e6=s.revenue_e6,
            raw=raw,
            context=self._context(s.ticker),
        )

    def _transfer(self, at_ns: int, kind: str, t: Transfer, raw: Mapping[str, Any]) -> Entry | None:
        fields = {"status": t.status, "type": t.type, "amount_e6": t.amount_e6, "fee_e6": t.fee_e6}
        if kind == "deposit":
            effect, counted = t.amount_e6 - t.fee_e6, DEPOSITED
        else:
            effect, counted = -(t.amount_e6 + t.fee_e6), WITHDRAWN
        if t.status in counted:
            return Entry(kind, at_ns, kind, t.id, fields=fields, cash_e6=effect, raw=raw)
        if t.status in UNDONE and self._ledger.has(kind, t.id):  # counted before: undo it
            return Entry(
                "adjustment",
                at_ns,
                f"{kind}_reversal",
                t.id,
                fields=fields,
                cash_e6=-effect,
                raw=raw,
                note=f"the {kind} was {t.status}",
            )
        return None  # not yet counted (a pending deposit) or never counted

    def _instance_transfer(
        self, at_ns: int, t: InstanceTransfer, raw: Mapping[str, Any]
    ) -> Entry | None:
        if t.status != "complete":
            return None
        effect = (t.amount_e6 if t.destination == EVENT_CONTRACT else 0) - (
            t.amount_e6 if t.source == EVENT_CONTRACT else 0
        )
        fields = {"from": t.source, "to": t.destination, "amount_e6": t.amount_e6}
        return Entry(
            "transfer", at_ns, "transfer", t.transfer_id, fields=fields, cash_e6=effect, raw=raw
        )

    # --- reconciliation ----------------------------------------------------------------------

    async def reconcile(
        self, held: Positions | None = None, book: Book | None = None
    ) -> Reconciliation:
        balance = await self._account.balance()
        held = held if held is not None else await self._account.positions()
        kalshi = {p.ticker: p.position_e2 for p in held.market_positions if p.position_e2}
        book = book if book is not None else replay(self._ledger)
        ours = book.held()
        differ = {
            t: (ours.get(t, 0), kalshi.get(t, 0))
            for t in sorted(set(ours) | set(kalshi))
            if ours.get(t, 0) != kalshi.get(t, 0)
        }
        result = Reconciliation(balance.balance_dollars_e6, book.cash_e6, differ)
        if result.difference_e6 < 0:  # less available than explained: resting buys hold some back
            try:
                reserved = await self._reserved_e6()
            except Exception as exc:  # unknown: the reconciliation is then simply not exact
                log.warning("resting orders could not be read", extra={"error": type(exc).__name__})
            else:
                result = Reconciliation(result.balance_e6, result.explained_e6, differ, reserved)
        fields = {
            "balance_e6": result.balance_e6,
            "explained_e6": result.explained_e6,
            "difference_e6": result.difference_e6,
            "reserved_e6": result.reserved_e6,
            "positions": {t: list(pair) for t, pair in differ.items()},
            "exact": result.exact,
        }
        last = self._ledger.last("reconciliation")
        if last is None or last["fields"] != fields:
            self._ledger.append(
                Entry("reconciliation", self._clock.now_ns(), "reconciliation", fields=fields)
            )
        return result

    async def _reserved_e6(self) -> int:
        """The cost of the owner's resting buy orders (rounded up to the millionth)."""
        total = 0
        async for page in self._account.orders(status="resting"):
            for order in page.items:
                if order.action != "buy":
                    continue
                price = order.yes_price_e6 if order.outcome_side == "yes" else order.no_price_e6
                total += -(-price * order.remaining_e2 // 100)
        return total
