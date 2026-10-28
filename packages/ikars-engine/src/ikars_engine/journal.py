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
from typing import Literal

import msgspec

from ikars_engine.model import Item, Ref
from ikars_engine.store import open_store

log = logging.getLogger("ikars_engine.journal")
KIND = "recommendation"
ACTIVE, EXPIRED, SUPERSEDED = "active", "expired", "superseded"
QUEUE_SIZE = 1_000
EVERY_S = 1.0  # how often the journal writes, expires and marks
RECENT = 200  # the most the API lists


class Recommendation(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    """A recommendation item's payload: what a signal plugin advises, and why (M1.12)."""

    ticker: str
    side: Literal["yes", "no"]
    entry_price_e6: int  # one contract of ``side`` at the moment: its ask
    count_e2: int  # the fixed size the P&L is for (paper only; D6)
    expires_ns: int
    summary: str  # one line for the Overview
    strategy: str  # the rule, as the plugin names it
    version: str  # the rule's registration hash (G4): a change is a new version
    horizons_s: list[int] = []  # when to mark it after it is made
    inputs: list[Ref] = []  # the items it was built from (provenance, F)
    pnl_e6: int | None = None  # after fees on entry and exit at the plugin's target (M1.12b)
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
)

_decode = msgspec.json.Decoder(Recommendation).decode
_encode = msgspec.json.Encoder().encode


@dataclass(frozen=True, slots=True)
class Mark:
    horizon_s: int
    at_ns: int
    yes_bid_e6: int | None
    yes_ask_e6: int | None


@dataclass(frozen=True, slots=True)
class Entry:
    id: str
    received_ns: int
    state: str
    superseded_by: str | None
    source: str
    recommendation: Recommendation
    marks: list[Mark]


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

    def add(self, item: Item) -> Recommendation:
        """Record a recommendation item; an earlier active one of the same strategy on the same
        market is superseded by it. Raises ``msgspec.ValidationError`` for a payload that is not a
        ``Recommendation`` (the plugin's fault, logged by the caller)."""
        rec = msgspec.convert(item.payload, Recommendation)
        with self._transaction():
            self._db.execute(
                "UPDATE recommendation SET state = ?, superseded_by = ?"
                " WHERE state = ? AND strategy = ? AND ticker = ? AND id != ?",
                (SUPERSEDED, item.ref.id, ACTIVE, rec.strategy, rec.ticker, item.ref.id),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO recommendation VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (item.ref.id, rec.strategy, rec.version, rec.ticker, rec.side, item.received_ns,
                 rec.expires_ns, ACTIVE, item.source, _encode(rec).decode()),
            )  # fmt: skip
        return rec

    def expire(self, now_ns: int) -> int:
        done = self._db.execute(
            "UPDATE recommendation SET state = ? WHERE state = ? AND expires_ns <= ?",
            (EXPIRED, ACTIVE, now_ns),
        )
        return done.rowcount

    def due(self, now_ns: int) -> list[tuple[str, str, int]]:
        """(id, ticker, horizon) of every mark whose time has come and is not written yet."""
        rows = self._db.execute(
            "SELECT id, ticker, received_ns, payload FROM recommendation"
            " WHERE received_ns >= ? ORDER BY received_ns",
            (now_ns - 86_400 * 1_000_000_000,),  # a mark is never more than a day late
        ).fetchall()
        written = {(i, h) for i, h in self._db.execute("SELECT id, horizon_s FROM mark")}
        due: list[tuple[str, str, int]] = []
        for rid, ticker, made_ns, payload in rows:
            for horizon in _decode(payload).horizons_s:
                if made_ns + horizon * 1_000_000_000 <= now_ns and (rid, horizon) not in written:
                    due.append((rid, ticker, horizon))
        return due

    def mark(self, rid: str, horizon_s: int, at_ns: int, bid: int | None, ask: int | None) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO mark VALUES (?, ?, ?, ?, ?)", (rid, horizon_s, at_ns, bid, ask)
        )

    def recent(self, limit: int = RECENT) -> list[Entry]:
        """The newest recommendations, newest first, with their marks."""
        rows = self._db.execute(
            "SELECT id, received_ns, state, superseded_by, source, payload FROM recommendation"
            " ORDER BY received_ns DESC LIMIT ?",
            (limit,),
        ).fetchall()
        marks: dict[str, list[Mark]] = {}
        if rows:
            ids = [r[0] for r in rows]
            holes = ",".join("?" * len(ids))
            for rid, horizon, at, bid, ask in self._db.execute(
                f"SELECT id, horizon_s, at_ns, yes_bid_e6, yes_ask_e6 FROM mark"  # noqa: S608
                f" WHERE id IN ({holes}) ORDER BY horizon_s",
                ids,
            ):
                marks.setdefault(rid, []).append(Mark(horizon, at, bid, ask))
        return [
            Entry(rid, made, state, by, source, _decode(payload), marks.get(rid, []))
            for rid, made, state, by, source, payload in rows
        ]


@dataclass
class JournalService:
    """Feeds the journal from the engine: ``listen`` is the engine's listener (it only queues);
    ``run`` writes, expires and marks once a second."""

    journal: Journal
    quotes: Mapping[str, tuple[int | None, int | None]]  # the live YES bid and ask, by ticker
    now_ns: Callable[[], int]
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
                self.journal.add(item)
                self.recorded += 1
            except Exception as exc:  # the plugin's payload or the file: counted, logged
                self.rejected += 1
                log.warning("recommendation not recorded",
                            extra={"ref": str(item.ref), "error": str(exc)})  # fmt: skip
        now = self.now_ns()
        self.journal.expire(now)
        for rid, ticker, horizon in self.journal.due(now):
            bid, ask = self.quotes.get(ticker, (None, None))
            self.journal.mark(rid, horizon, now, bid, ask)

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

    def health(self) -> dict[str, object]:
        return {"recorded": self.recorded, "rejected": self.rejected, "dropped": self.dropped,
                "failures": self.failures}  # fmt: skip
