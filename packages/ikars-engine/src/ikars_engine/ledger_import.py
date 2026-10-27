"""The owner's Kalshi account into the ledger, and the ledger against Kalshi (PLAN D18).

**Import.** The first import reads the account's whole history (fills live and historical,
settlements, deposits, withdrawals, transfers between the account's exchange instances); later ones
read fills and settlements from a day before the newest already in, and every transfer (there are
few). New records are appended in Kalshi's time order; one already in is skipped, so an import can
run any number of times. Manual trades are fills like any other.

**Cash.** Each record carries its effect on the account's cash (``cash_e6``), computed exactly in
dollar millionths. Kalshi nets YES and NO in a market, so a fill that adds YES exposure while NO is
held **closes** NO contracts (the account receives the NO price for each) before it **opens** YES
contracts (paying the YES price), and the other way round; the fee is paid on top. A settlement pays
its revenue less its fee and closes the market's position; a deposit adds its amount less its fee;
a withdrawal takes its amount and fee from when it is requested, and a failed or returned one is
reversed by a new record. A transfer counts only when it moves money into or out of the
event-contract instance.

**Reconciliation.** After each import the cash the ledger explains is compared with Kalshi's
balance, and its positions with Kalshi's. A difference is never fixed: it is written to the ledger
as a reconciliation record (only when the result changes) and shown in health as a count. Kalshi
reports no interest (M1.2), so interest arrives as an unexplained difference until the owner labels
it with ``ledger annotate``.

Logs carry counts only; amounts, tickers and ids stay in the ledger.
"""

import logging
from collections import Counter
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ikars_core.account import Account, Fill, InstanceTransfer, Settlement, Transfer
from ikars_core.endpoints import TypedPage

from ikars_engine.clocks import EventClock
from ikars_engine.ledger import Entry, Ledger, LedgerError

log = logging.getLogger("ikars_engine.ledger")
NS = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MICROSECOND = timedelta(microseconds=1)
OVERLAP_S = 86_400  # each later import re-reads a day of fills and settlements
FIRST_DONE = "import.first_done"
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


@dataclass(frozen=True, slots=True)
class Reconciliation:
    balance_e6: int
    explained_e6: int
    positions: dict[str, tuple[int, int]]  # ticker -> (ledger, Kalshi), only where they differ

    @property
    def difference_e6(self) -> int:
        return self.balance_e6 - self.explained_e6

    @property
    def exact(self) -> bool:
        return self.difference_e6 == 0 and not self.positions


@dataclass(slots=True)
class ImportReport:
    added: Counter[str] = field(default_factory=Counter)
    reconciliation: Reconciliation | None = None


def replay(ledger: Ledger) -> tuple[dict[str, int], int]:
    """Each market's net position (hundredths; negative: NO; flat markets left out) and the cash
    the ledger explains, from its records in sequence order."""
    held: dict[str, int] = {}
    cash = 0
    for body in ledger.records():
        ticker = body.get("ticker")
        if body["kind"] == "fill" and ticker:
            held[ticker] = held.get(ticker, 0) + int(body["fields"]["delta_e2"])
        elif body["kind"] == "settlement" and ticker:
            held[ticker] = 0
        if body.get("cash_e6") is not None:
            cash += int(body["cash_e6"])
    return {t: p for t, p in held.items() if p}, cash


class AccountImporter:
    def __init__(
        self,
        ledger: Ledger,
        account: Account,
        clock: EventClock,
        describe: Describe | None = None,
    ) -> None:
        self._ledger = ledger
        self._account = account
        self._clock = clock
        self._describe = describe

    async def run_once(self) -> ImportReport:
        first = self._ledger.bookmark(FIRST_DONE) is None
        found = await self._read(first)
        found.sort(key=lambda item: (item[0], item[1]))
        positions, _ = replay(self._ledger)
        entries: list[Entry] = []
        seen: set[tuple[str, str]] = set()
        for at_ns, _, model, raw in found:
            entry = self._entry(at_ns, model, raw, positions)
            if entry is None or entry.source_id is None:
                continue
            key = (entry.source, entry.source_id)
            if key in seen or self._ledger.has(*key):
                continue
            seen.add(key)
            self._move(entry, positions)
            entries.append(entry)
        self._ledger.append_all(entries)
        if first:
            self._ledger.set_bookmark(FIRST_DONE, str(self._clock.now_ns()))
        reconciliation = await self.reconcile()
        log.info("ledger import", extra={"added": len(entries), "exact": reconciliation.exact})
        return ImportReport(Counter(e.kind for e in entries), reconciliation)

    async def _read(self, first: bool) -> list[Found]:
        """Everything to consider, as (time, order within a time, model, raw JSON)."""
        found: list[Found] = []
        since = None if first else self._since()
        fills = [self._account.fills(min_ts=since)]
        if first:
            fills.append(self._account.fills(historical=True))
        for pages in fills:
            found += await _gather(pages, 0, lambda f: ns_of(f.created_time))
        settled = self._account.settlements(min_ts=since)
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

    def _since(self) -> int | None:
        latest = [self._ledger.latest_at_ns(s) for s in ("fill", "settlement")]
        known = [at for at in latest if at is not None]
        return max(0, min(known) // NS - OVERLAP_S) if known else None

    # --- records -----------------------------------------------------------------------------

    def _entry(
        self, at_ns: int, model: Any, raw: Mapping[str, Any], positions: Mapping[str, int]
    ) -> Entry | None:
        if isinstance(model, Fill):
            return self._fill(at_ns, model, raw, positions.get(model.ticker, 0))
        if isinstance(model, Settlement):
            return self._settlement(at_ns, model, raw)
        if isinstance(model, InstanceTransfer):
            return self._instance_transfer(at_ns, model, raw)
        kind, transfer = model
        return self._transfer(at_ns, kind, transfer, raw)

    def _context(self, ticker: str) -> dict[str, str]:
        described = self._describe(ticker) if self._describe is not None else None
        return dict(described or {})

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
            cash_e6=s.revenue_e6 - s.fee_e6,
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

    @staticmethod
    def _move(entry: Entry, positions: dict[str, int]) -> None:
        if entry.ticker is None:
            return
        if entry.kind == "fill":
            positions[entry.ticker] = positions.get(entry.ticker, 0) + int(entry.fields["delta_e2"])
        elif entry.kind == "settlement":
            positions[entry.ticker] = 0

    # --- reconciliation ----------------------------------------------------------------------

    async def reconcile(self) -> Reconciliation:
        balance = await self._account.balance()
        held = await self._account.positions()
        kalshi = {p.ticker: p.position_e2 for p in held.market_positions if p.position_e2}
        ours, explained = replay(self._ledger)
        differ = {
            t: (ours.get(t, 0), kalshi.get(t, 0))
            for t in sorted(set(ours) | set(kalshi))
            if ours.get(t, 0) != kalshi.get(t, 0)
        }
        result = Reconciliation(balance.balance_dollars_e6, explained, differ)
        fields = {
            "balance_e6": result.balance_e6,
            "explained_e6": result.explained_e6,
            "difference_e6": result.difference_e6,
            "positions": {t: list(pair) for t, pair in differ.items()},
            "exact": result.exact,
        }
        last = self._ledger.last("reconciliation")
        if last is None or last["fields"] != fields:
            self._ledger.append(
                Entry("reconciliation", self._clock.now_ns(), "reconciliation", fields=fields)
            )
        return result
