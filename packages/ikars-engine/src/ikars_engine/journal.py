"""The recommendation journal (M1.12a; PLAN D20 F and G9, D21-2): every recommendation IKARS makes,
with its provenance and its lifecycle, scored forward from the moment it is made.

A signal plugin emits an item of kind ``recommendation`` whose payload is a ``Recommendation``. The
engine hands it to the journal through a listener (which only queues it: a listener must never
block a plugin); the journal's own task writes it once a second (a few rows: on the event loop,
as the state store does).

- **Lifecycle (G9):** ``active`` until it expires, or until the same strategy recommends the same
  market again (``superseded``, pointing at the newer one).
- **Marks:** at each of the recommendation's horizons (30 and 60 minutes for momentum) the
  market's YES bid and ask are written beside it. M1.12b turns a mark into the score after fees.
- **Provenance (F):** the strategy, its registered version (a hash, G4), and the items it was
  built from, kept with it for ever.

``journal.sqlite`` lives in the data folder, apart from the tax ledger: it records what IKARS
advised, not money that moved. It is backed up with the state.
"""

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import msgspec
from msgspec import Meta

from ikars_engine.model import Item, Ref
from ikars_engine.store import open_store

log = logging.getLogger("ikars_engine.journal")
KIND = "recommendation"
ACTIVE, EXPIRED, SUPERSEDED = "active", "expired", "superseded"
QUEUE_SIZE = 1_000
EVERY_S = 1.0  # how often the journal writes, expires and marks
RECENT = 200  # the most the API lists
DAY_NS = 86_400 * 1_000_000_000
LONGEST_NS = 30 * DAY_NS  # the furthest a recommendation may expire or be marked after it is made
QUOTE_WAIT_NS = 600 * 1_000_000_000  # a missing quote: the mark waits this long for one
SETTLE_EVERY_NS = 60 * 1_000_000_000  # how often settlements are looked up
# bounds every integer the terminal reads exactly (JavaScript: 2^53) and a price can take
Price = Annotated[int, Meta(ge=1, le=1_000_000)]  # one contract: 1 millionth of a dollar to $1
Count = Annotated[int, Meta(ge=1, le=1_000_000_000)]
Money = Annotated[int, Meta(ge=-(10**12), le=10**12)]
Horizon = Annotated[int, Meta(ge=1, le=LONGEST_NS // 1_000_000_000)]


class Recommendation(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A recommendation item's payload: what a signal plugin advises, and why (M1.12)."""

    ticker: str
    side: Literal["yes", "no"]
    entry_price_e6: Price  # one contract of ``side`` at the moment: its ask
    count_e2: Count  # the fixed size the P&L is for (paper only; D6)
    expires_ns: int
    summary: str  # one line for the Overview
    strategy: str  # the rule, as the plugin names it
    version: str  # the rule's registration hash (G4): a change is a new version
    horizons_s: list[Horizon] = []  # when to mark it after it is made
    inputs: list[Ref] = []  # the items it was built from (provenance, F)
    pnl_e6: Money | None = None  # after fees on entry and exit at the plugin's target (M1.12b)
    event_ticker: str = ""


JOURNAL_MIGRATIONS = (
    """
    CREATE TABLE recommendation (
        id TEXT PRIMARY KEY,
        strategy TEXT NOT NULL,
        version TEXT NOT NULL,
        ticker TEXT NOT NULL,
        side TEXT NOT NULL,
        received_ns INTEGER NOT NULL,   -- when IKARS made it (the host's clock)
        expires_ns INTEGER NOT NULL,
        state TEXT NOT NULL,            -- active, expired, superseded
        superseded_by TEXT,
        source TEXT NOT NULL,           -- the plugin that emitted it
        payload TEXT NOT NULL           -- the Recommendation, as emitted
    );
    CREATE TABLE pending_mark (         -- a horizon not marked yet, and when it falls due
        id TEXT NOT NULL REFERENCES recommendation (id),
        horizon_s INTEGER NOT NULL,
        due_ns INTEGER NOT NULL,
        ticker TEXT NOT NULL,
        PRIMARY KEY (id, horizon_s)
    );
    CREATE INDEX pending_mark_due ON pending_mark (due_ns);
    CREATE INDEX recommendation_active ON recommendation (state) WHERE state = 'active';
    CREATE INDEX recommendation_made ON recommendation (received_ns);
    CREATE TABLE mark (
        id TEXT NOT NULL REFERENCES recommendation (id),
        horizon_s INTEGER NOT NULL,
        at_ns INTEGER NOT NULL,
        yes_bid_e6 INTEGER,             -- None: no bid at that moment
        yes_ask_e6 INTEGER,
        PRIMARY KEY (id, horizon_s)
    );
    """,
    # 2 (M1.12b live check, owner): a market's settlement, so a check made after it closed is
    # scored at $1 or $0 rather than left unscored (migration 1 shipped in v0.1.7)
    """
    CREATE TABLE settlement (
        ticker TEXT PRIMARY KEY,
        result TEXT NOT NULL,           -- yes or no
        at_ns INTEGER NOT NULL          -- when IKARS learned it
    );
    -- whether the market had stopped trading when the mark was made: 1 closed, 0 open, NULL not
    -- known (marks before this column); only a closed one is scored at its settlement
    ALTER TABLE mark ADD COLUMN closed INTEGER;
    """,
)

_decode = msgspec.json.Decoder(Recommendation).decode
_encode = msgspec.json.Encoder().encode


@dataclass(frozen=True, slots=True)
class Mark:
    horizon_s: int
    at_ns: int
    yes_bid_e6: int | None
    yes_ask_e6: int | None
    closed: bool | None = None  # the market had stopped trading then (None: not known)


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    received_ns: int
    state: str
    superseded_by: str | None
    source: str
    recommendation: Recommendation
    marks: list[Mark]
    settled: str | None = None  # the market's result once it settled: yes or no


class Journal:
    """``journal.sqlite``, on the engine's event loop (written by the service, read by the API)."""

    def __init__(self, path: Path) -> None:
        self._db = open_store(path, JOURNAL_MIGRATIONS)

    def close(self) -> None:
        self._db.close()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """All or nothing (the connection is in autocommit mode: the store's)."""
        self._db.execute("BEGIN")
        try:
            yield self._db
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def add(self, item: Item) -> bool:
        """Record a recommendation item; an earlier active one of the same strategy on the same
        market is superseded by it. False: that id is recorded already (nothing changes). Raises
        ``msgspec.ValidationError`` or ``ValueError`` for a payload that is not a sound
        ``Recommendation`` (the plugin's fault, logged by the caller)."""
        rec = msgspec.convert(item.payload, Recommendation)
        if not item.received_ns < rec.expires_ns <= item.received_ns + LONGEST_NS:
            raise ValueError("it must expire after it is made, within 30 days")
        if rec.count_e2 % 100:  # whole contracts: every amount is then exact (merge review 1)
            raise ValueError("a recommendation is for whole contracts")
        known = "SELECT 1 FROM recommendation WHERE id = ?"
        if self._db.execute(known, (item.ref.id,)).fetchone():  # merge review 1
            return False
        with self._transaction():
            self._db.execute(
                "UPDATE recommendation SET state = ?, superseded_by = ?"
                " WHERE state = ? AND strategy = ? AND ticker = ? AND id != ? AND expires_ns > ?",
                (
                    SUPERSEDED,
                    item.ref.id,
                    ACTIVE,
                    rec.strategy,
                    rec.ticker,
                    item.ref.id,
                    item.received_ns,
                ),  # fmt: skip
            )
            self._db.execute(
                "INSERT INTO recommendation VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (item.ref.id, rec.strategy, rec.version, rec.ticker, rec.side, item.received_ns,
                 rec.expires_ns, ACTIVE, item.source, _encode(rec).decode()),
            )  # fmt: skip
            self._db.executemany(
                "INSERT INTO pending_mark VALUES (?, ?, ?, ?)",
                [(item.ref.id, h, item.received_ns + h * 1_000_000_000, rec.ticker)
                 for h in sorted(set(rec.horizons_s))],
            )  # fmt: skip
        return True

    def expire(self, now_ns: int) -> int:
        done = self._db.execute(
            "UPDATE recommendation SET state = ? WHERE state = ? AND expires_ns <= ?",
            (EXPIRED, ACTIVE, now_ns),
        )
        return done.rowcount

    def due(self, now_ns: int) -> list[tuple[str, str, int, int]]:
        """(id, ticker, horizon, due_ns) of every mark whose time has come (an index lookup; a
        mark the engine was down for is still due, and its ``at_ns`` shows how late)."""
        rows = self._db.execute(
            "SELECT id, ticker, horizon_s, due_ns FROM pending_mark WHERE due_ns <= ?"
            " ORDER BY due_ns",
            (now_ns,),
        )
        return [(rid, ticker, horizon, due) for rid, ticker, horizon, due in rows]

    def mark(
        self, rid: str, horizon_s: int, at_ns: int, bid: int | None, ask: int | None,
        closed: bool | None = None,
    ) -> None:  # fmt: skip
        with self._transaction():
            self._db.execute(
                "INSERT OR IGNORE INTO mark (id, horizon_s, at_ns, yes_bid_e6, yes_ask_e6, closed)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (rid, horizon_s, at_ns, bid, ask, closed),
            )
            self._db.execute(
                "DELETE FROM pending_mark WHERE id = ? AND horizon_s = ?", (rid, horizon_s)
            )

    def active(self, strategy: str, ticker: str, now_ns: int) -> bool:
        """Whether ``strategy`` has a recommendation on ``ticker`` still valid at ``now_ns``."""
        row = self._db.execute(
            "SELECT 1 FROM recommendation WHERE state = ? AND strategy = ? AND ticker = ?"
            " AND expires_ns > ? LIMIT 1",
            (ACTIVE, strategy, ticker, now_ns),
        ).fetchone()
        return row is not None

    def unsettled(self, since_ns: int) -> list[str]:
        """Markets recommended since ``since_ns`` whose settlement is not known yet."""
        rows = self._db.execute(
            "SELECT DISTINCT r.ticker FROM recommendation r LEFT JOIN settlement s"
            " ON s.ticker = r.ticker WHERE s.ticker IS NULL AND r.received_ns >= ?",
            (since_ns,),
        )
        return [ticker for (ticker,) in rows]

    def settle(self, ticker: str, result: str, at_ns: int) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO settlement VALUES (?, ?, ?)", (ticker, result, at_ns)
        )

    def recent(self, limit: int = RECENT) -> list[Entry]:
        """The newest recommendations, newest first, with their marks and settlements."""
        rows = self._db.execute(
            "SELECT r.id, r.received_ns, r.state, r.superseded_by, r.source, r.payload, s.result"
            " FROM recommendation r LEFT JOIN settlement s ON s.ticker = r.ticker"
            " ORDER BY r.received_ns DESC, r.rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
        marks: dict[str, list[Mark]] = {}
        if rows:
            ids = [r[0] for r in rows]
            holes = ",".join("?" * len(ids))
            for rid, horizon, at, bid, ask, closed in self._db.execute(
                f"SELECT id, horizon_s, at_ns, yes_bid_e6, yes_ask_e6, closed FROM mark"  # noqa: S608
                f" WHERE id IN ({holes}) ORDER BY horizon_s",
                ids,
            ):
                shut = None if closed is None else bool(closed)
                marks.setdefault(rid, []).append(Mark(horizon, at, bid, ask, shut))
        return [
            Entry(rid, made, state, by, source, _decode(payload), marks.get(rid, []), settled)
            for rid, made, state, by, source, payload, settled in rows
        ]


@dataclass
class JournalService:
    """Feeds the journal from the engine: ``listen`` is the engine's listener (it only queues);
    ``run`` writes, expires and marks once a second."""

    journal: Journal
    quotes: Mapping[str, tuple[int | None, int | None]]  # the live YES bid and ask, by ticker
    now_ns: Callable[[], int]
    # a market's result once it settled ("yes" or "no"; the market index's), else None
    result: Callable[[str], str | None] = lambda ticker: None
    # whether a market still trades (True), has stopped (False), or is not known (None)
    trading: Callable[[str], bool | None] = lambda ticker: None
    settled_at_ns: int = 0
    queue: asyncio.Queue[Item] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    dropped: int = 0
    rejected: int = 0
    recorded: int = 0
    failures: int = 0  # passes whose expiring or marking failed (said in health; retried next)

    def listen(self, item: Item) -> None:
        if item.ref.kind != KIND:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:  # never blocks a plugin; counted and shown in health
            self.dropped += 1

    def _write(self, items: list[Item]) -> None:
        for item in items:  # one at a time: a bad one never loses the rest of the batch
            try:
                if self.journal.add(item):
                    self.recorded += 1
            except Exception as exc:  # the plugin's payload or the file: counted, logged
                self.rejected += 1
                log.warning("recommendation not recorded",
                            extra={"ref": str(item.ref), "error": str(exc)})  # fmt: skip
        now = self.now_ns()
        self.journal.expire(now)
        for rid, ticker, horizon, due in self.journal.due(now):
            bid, ask = self.quotes.get(ticker, (None, None))
            if bid is None and ask is None and now - due < QUOTE_WAIT_NS:
                continue  # no quote yet (a fresh start, a quiet market): marked when one comes
            open_now = self.trading(ticker)
            self.journal.mark(
                rid, horizon, now, bid, ask, None if open_now is None else not open_now
            )
        if now - self.settled_at_ns >= SETTLE_EVERY_NS:  # once a minute, from the market index
            self.settled_at_ns = now
            for ticker in self.journal.unsettled(now - LONGEST_NS):
                result = self.result(ticker)
                if result in ("yes", "no"):
                    self.journal.settle(ticker, result, now)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(EVERY_S)
            items: list[Item] = []
            while not self.queue.empty():
                items.append(self.queue.get_nowait())
            try:
                self._write(items)
            except Exception as exc:  # expiring or marking: never stops the engine; next second
                self.failures += 1
                log.warning("journal pass failed", extra={"error": f"{type(exc).__name__}: {exc}"})

    def flush(self) -> None:
        """At shutdown, once the plugins have stopped: what is still queued is written."""
        items: list[Item] = []
        while not self.queue.empty():
            items.append(self.queue.get_nowait())
        self._write(items)

    def health(self) -> dict[str, object]:
        return {"recorded": self.recorded, "rejected": self.rejected, "dropped": self.dropped,
                "failures": self.failures}  # fmt: skip
