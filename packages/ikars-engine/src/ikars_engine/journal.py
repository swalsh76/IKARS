"""The recommendation journal (M1.12a; PLAN D20 F and G9, D21-2): every recommendation IKARS makes,
with its provenance and its lifecycle, scored forward from the moment it is made.

A signal plugin emits an item of kind ``recommendation`` whose payload is a ``Recommendation``. The
engine hands it to the journal through a listener (which only queues it: a listener must never
block a plugin); the journal's own task writes it once a second (a few rows: on the event loop,
as the state store does).

- **Lifecycle (G9):** ``active`` until it expires, or until the same strategy recommends the same
  market again (``superseded``, pointing at the newer one).
- **Marks:** at each of the recommendation's horizons (30 and 60 minutes for momentum) the
  market's YES bid and ask are written beside it. M1.12b turns a mark into the score after fees;
  from M1.12c the score is kept with the mark once it is known (at once, or at the market's
  settlement), so the scorecard is a query and an old check keeps its score when its market has
  left the index.
- **The owner's response (M1.12c, G9):** taken, ignored or dismissed; the latest one counts.
  "Taken" is the owner's word only: IKARS places nothing (D6).
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
from ikars_core.fees import FeeSchedule
from msgspec import Meta

from ikars_engine.model import Item, Ref
from ikars_engine.pnl import mark_score_e6
from ikars_engine.recommendations import CARD_DAYS, Board, Notices, ScoreLine, figures, scorecard
from ikars_engine.store import open_store
from ikars_engine.threads import in_daemon_thread

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
FEES_WAIT_NS = 3_600 * 1_000_000_000  # a check whose fees are not known waits this long
SCORE_BATCH = 500  # the most checks scored in one pass (a backlog is worked off a second at a time)
ANSWERS = ("taken", "ignored", "dismissed")  # the owner's response (G9)
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
    # 3 (M1.12c): the owner's response, and each check's score once it is known (scored 1: done,
    # its score None when there was no price; NULL: not yet, as for a closed market not settled)
    """
    CREATE TABLE response (
        id TEXT PRIMARY KEY REFERENCES recommendation (id),
        answer TEXT NOT NULL,           -- taken, ignored or dismissed
        at_ns INTEGER NOT NULL
    );
    ALTER TABLE mark ADD COLUMN scored INTEGER;
    ALTER TABLE mark ADD COLUMN score_e6 INTEGER;
    ALTER TABLE mark ADD COLUMN at_settlement INTEGER;
    CREATE INDEX mark_unscored ON mark (id) WHERE scored IS NULL;
    """,
)
# an entry with its settlement and response; the WHERE and ORDER come from this module only
_ENTRIES = (
    "SELECT r.id, r.received_ns, r.state, r.superseded_by, r.source, r.payload, s.result, a.answer"
    " FROM recommendation r LEFT JOIN settlement s ON s.ticker = r.ticker"
    " LEFT JOIN response a ON a.id = r.id"
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
    scored: bool = False  # its score is kept (the score None: there was no price)
    score_e6: int | None = None
    at_settlement: bool = False


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
    response: str | None = None  # the owner's: taken, ignored or dismissed


class Journal:
    """``journal.sqlite``, on the engine's event loop (written by the service, read by the API)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db = open_store(path, JOURNAL_MIGRATIONS)
        self.changes = 0  # counts every change to what is active or answered (the API's cache key)

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

    def add(self, item: Item) -> Recommendation | None:
        """Record a recommendation item; an earlier active one of the same strategy on the same
        market is superseded by it. Returns it; None: that id is recorded already (nothing
        changes). Raises
        ``msgspec.ValidationError`` or ``ValueError`` for a payload that is not a sound
        ``Recommendation`` (the plugin's fault, logged by the caller)."""
        rec = msgspec.convert(item.payload, Recommendation)
        if not item.received_ns < rec.expires_ns <= item.received_ns + LONGEST_NS:
            raise ValueError("it must expire after it is made, within 30 days")
        if rec.count_e2 % 100:  # whole contracts: every amount is then exact (merge review 1)
            raise ValueError("a recommendation is for whole contracts")
        known = "SELECT 1 FROM recommendation WHERE id = ?"
        if self._db.execute(known, (item.ref.id,)).fetchone():  # merge review 1
            return None
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
        self.changes += 1
        return rec

    def expire(self, now_ns: int) -> int:
        done = self._db.execute(
            "UPDATE recommendation SET state = ? WHERE state = ? AND expires_ns <= ?",
            (EXPIRED, ACTIVE, now_ns),
        )
        self.changes += done.rowcount > 0
        return done.rowcount

    def respond(self, rid: str, answer: str | None, at_ns: int) -> bool:
        """The owner's response to ``rid`` (None takes it back); False: no such recommendation."""
        if answer is not None and answer not in ANSWERS:
            raise ValueError(f"no such answer {answer!r}")
        if self._db.execute("SELECT 1 FROM recommendation WHERE id = ?", (rid,)).fetchone() is None:
            return False
        if answer is None:
            self._db.execute("DELETE FROM response WHERE id = ?", (rid,))
        else:
            self._db.execute(
                "INSERT OR REPLACE INTO response VALUES (?, ?, ?)", (rid, answer, at_ns)
            )
        self.changes += 1
        return True

    def score(
        self, fees: Callable[[str], FeeSchedule | None], now_ns: int, limit: int = SCORE_BATCH
    ) -> int:
        """Keeps the score of each check not scored yet, as the API scores it (``fees`` by ticker).
        A closed market's check waits for its settlement; one whose fees are not known waits too,
        up to an hour (a gap in the index), then is kept unscored (its market has left the index).
        Returns how many were kept."""
        rows = self._db.execute(
            "SELECT m.id, m.horizon_s, m.yes_bid_e6, m.yes_ask_e6, m.closed, r.payload, s.result,"
            " m.at_ns"
            " FROM mark m JOIN recommendation r ON r.id = m.id"
            " LEFT JOIN settlement s ON s.ticker = r.ticker"
            " WHERE m.scored IS NULL AND NOT (m.closed = 1 AND s.ticker IS NULL) LIMIT ?",
            (limit,),
        ).fetchall()
        done: list[tuple[int | None, int, str, int]] = []
        for rid, horizon, bid, ask, closed, payload, settled, at in rows:
            rec = _decode(payload)
            schedule = fees(rec.ticker)
            if schedule is None:  # not known now: tried again, or given up after an hour (review)
                if now_ns - at >= FEES_WAIT_NS:
                    done.append((None, 0, rid, horizon))
                continue
            value, at_settlement = mark_score_e6(
                schedule, rec.side, rec.count_e2, rec.entry_price_e6, bid, ask,
                None if closed is None else bool(closed), settled,
            )  # fmt: skip
            done.append((value, int(at_settlement), rid, horizon))
        if done:
            with self._transaction():
                self._db.executemany(
                    "UPDATE mark SET scored = 1, score_e6 = ?, at_settlement = ?"
                    " WHERE id = ? AND horizon_s = ?",
                    done,
                )
        return len(done)

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
        return self._entries(" ORDER BY r.received_ns DESC, r.rowid DESC LIMIT ?", (limit,))

    def get(self, rid: str) -> Entry | None:
        found = self._entries(" WHERE r.id = ?", (rid,))
        return found[0] if found else None

    def entries(self, ids: list[str]) -> list[Entry]:
        """These recommendations, in this order (an unknown id is left out)."""
        if not ids:
            return []
        holes = ",".join("?" * len(ids))
        found = {e.id: e for e in self._entries(f" WHERE r.id IN ({holes})", tuple(ids))}
        return [found[rid] for rid in ids if rid in found]

    def active_entries(self) -> list[Entry]:
        return self._entries(" WHERE r.state = ?", (ACTIVE,))

    def _entries(self, rest: str, params: tuple[object, ...]) -> list[Entry]:
        rows = self._db.execute(_ENTRIES + rest, params).fetchall()
        marks: dict[str, list[Mark]] = {}
        if rows:
            ids = [r[0] for r in rows]
            holes = ",".join("?" * len(ids))
            for rid, horizon, at, bid, ask, closed, scored, value, settles in self._db.execute(
                "SELECT id, horizon_s, at_ns, yes_bid_e6, yes_ask_e6, closed, scored, score_e6,"  # noqa: S608
                f" at_settlement FROM mark WHERE id IN ({holes}) ORDER BY horizon_s",
                ids,
            ):
                shut = None if closed is None else bool(closed)
                marks.setdefault(rid, []).append(
                    Mark(horizon, at, bid, ask, shut, bool(scored), value, bool(settles))
                )
        return [
            Entry(
                rid, made, state, by, source, _decode(payload), marks.get(rid, []), settled, answer
            )  # fmt: skip
            for rid, made, state, by, source, payload, settled, answer in rows
        ]


@dataclass
class JournalService:
    """Feeds the journal from the engine: ``listen`` is the engine's listener (it only queues);
    ``run`` writes, expires, marks and scores once a second, ranks the active ones on the board
    (offering the first to ``notices``) and works out the scorecard once a minute (M1.12c)."""

    journal: Journal
    quotes: Mapping[str, tuple[int | None, int | None]]  # the live YES bid and ask, by ticker
    now_ns: Callable[[], int]
    # a market's result once it settled ("yes" or "no"; the market index's), else None
    result: Callable[[str], str | None] = lambda ticker: None
    # whether a market still trades (True), has stopped (False), or is not known (None)
    trading: Callable[[str], bool | None] = lambda ticker: None
    # a market's fee schedule, by ticker (None: not known)
    fees: Callable[[str], FeeSchedule | None] = lambda ticker: None
    notices: Notices | None = None
    board: Board = field(default_factory=Board)
    card: list[ScoreLine] = field(default_factory=list)
    card_at_ns: int = 0
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

    def load(self) -> None:
        """The board from the journal's active recommendations (at start)."""
        for entry in self.journal.active_entries():
            self._place(entry.id, entry.recommendation, entry.received_ns)
            self.board.hide(entry.id, entry.response == "dismissed")

    def _place(self, rid: str, rec: Recommendation, made_ns: int) -> bool:
        found = figures(rec, made_ns, self.fees(rec.ticker))
        return self.board.put(rid, rec, None if found is None else found.per_day_e6)

    def respond(self, rid: str, answer: str | None) -> bool:
        """The owner's response; False: no such recommendation. A dismissed one leaves the list."""
        if not self.journal.respond(rid, answer, self.now_ns()):
            return False
        self.board.hide(rid, answer == "dismissed")
        return True

    def _write(self, items: list[Item]) -> None:
        for item in items:  # one at a time: a bad one never loses the rest of the batch
            try:
                rec = self.journal.add(item)
            except Exception as exc:  # the plugin's payload or the file: counted, logged
                self.rejected += 1
                log.warning("recommendation not recorded",
                            extra={"ref": str(item.ref), "error": str(exc)})  # fmt: skip
                continue
            if rec is None:
                continue
            self.recorded += 1
            if self._place(item.ref.id, rec, item.received_ns) and self.notices is not None:
                self.notices.offer(rec)
        now = self.now_ns()
        self.journal.expire(now)
        self.board.prune(now)
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
        self.journal.score(self.fees, now)

    async def refresh_card(self) -> None:
        """The scorecard, on a worker thread (its own read-only connection)."""
        now = self.now_ns()
        self.card_at_ns = now
        self.card = await in_daemon_thread(scorecard, self.journal.path, now - CARD_DAYS * DAY_NS)

    async def run(self) -> None:
        try:
            self.load()
        except Exception as exc:  # the list starts empty and fills as recommendations come
            self.failures += 1
            log.warning("journal board not loaded", extra={"error": f"{type(exc).__name__}: {exc}"})
        while True:
            await asyncio.sleep(EVERY_S)
            if self.now_ns() - self.card_at_ns >= SETTLE_EVERY_NS:
                try:
                    await self.refresh_card()
                except Exception as exc:  # said in health; tried again in a minute
                    self.failures += 1
                    log.warning("scorecard failed", extra={"error": f"{type(exc).__name__}: {exc}"})
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
