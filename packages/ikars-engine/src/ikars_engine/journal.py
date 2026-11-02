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
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

import msgspec
from ikars_core.fees import FeeSchedule
from msgspec import Meta

from ikars_engine.model import Item, Ref
from ikars_engine.pnl import mark_score_e6
from ikars_engine.recommendations import (
    CARD_DAYS,
    QUOTE_WAIT_NS,
    Board,
    Notices,
    ScoreLine,
    figures,
    scorecard,
)
from ikars_engine.store import open_store, transaction
from ikars_engine.threads import in_daemon_thread

log = logging.getLogger("ikars_engine.journal")
KIND = "recommendation"
ACTIVE, EXPIRED, SUPERSEDED = "active", "expired", "superseded"
QUEUE_SIZE = 1_000
EVERY_S = 1.0  # how often the journal writes, expires and marks
RECENT = 200  # the most the API lists
DAY_NS = 86_400 * 1_000_000_000
LONGEST_NS = 30 * DAY_NS  # the furthest a recommendation may expire or be marked after it is made
SETTLE_EVERY_NS = 60 * 1_000_000_000  # how often settlements are looked up
FEES_WAIT_NS = 3_600 * 1_000_000_000  # a check whose fees are not known waits this long
SCORE_BATCH = 500  # the most checks scored in one pass (a backlog is worked off a second at a time)
SCORE_PAGES = 4  # the most pages of waiting checks one pass reads past (merge review 3)
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
    CREATE INDEX mark_unscored ON mark (at_ns) WHERE scored IS NULL;
    """,
    # 4 (M1.14 live check, owner): what a YES contract paid, so a scalar result (a fraction, such as
    # 50c on a walkover) is scored too; the settlements before it were all yes ($1) or no (nothing)
    """
    ALTER TABLE settlement ADD COLUMN yes_paid_e6 INTEGER;
    UPDATE settlement SET yes_paid_e6 = CASE result WHEN 'yes' THEN 1000000 ELSE 0 END;
    """,
    # 5 (M1.R3 C4, owner 2026-11-02): a check on a closed market is scored at its settlement only,
    # never sold at a quote kept from before it closed; the ones scored so are scored again
    """
    UPDATE mark SET scored = NULL, score_e6 = NULL, at_settlement = NULL
        WHERE closed = 1 AND scored = 1 AND at_settlement = 0;
    """,
)
# an entry with its settlement and response; the WHERE and ORDER come from this module only
_ENTRIES = (
    "SELECT r.id, r.received_ns, r.state, r.superseded_by, r.source, r.payload, s.result,"
    " s.yes_paid_e6, a.answer FROM recommendation r LEFT JOIN settlement s ON s.ticker = r.ticker"
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
    settled: str | None = None  # the market's result once it settled: yes, no or scalar
    response: str | None = None  # the owner's: taken, ignored or dismissed
    yes_paid_e6: int | None = None  # what a YES contract paid at that settlement


class Journal:
    """``journal.sqlite``, on the engine's event loop (written by the service, read by the API)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db = open_store(path, JOURNAL_MIGRATIONS)
        self.changes = 0  # counts every change to what is active or answered (the API's cache key)
        # the markets not settled yet, each with its newest recommendation's time: read once, then
        # kept by add and settle (a query twice a minute cost ~95 ms at 30 days: M1.R3 C6)
        self._unsettled: dict[str, int] | None = None

    def close(self) -> None:
        self._db.close()

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
        with transaction(self._db):
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
        if self._unsettled is not None:
            settled = "SELECT 1 FROM settlement WHERE ticker = ?"
            if self._db.execute(settled, (rec.ticker,)).fetchone() is None:
                newest = max(self._unsettled.get(rec.ticker, item.received_ns), item.received_ns)
                self._unsettled[rec.ticker] = newest
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
        self,
        fees: Callable[[str], FeeSchedule | None],
        now_ns: int,
        limit: int = SCORE_BATCH,
        waited_from: int = 0,
    ) -> int:
        """Keeps the score of each check not scored yet, as the API scores it (``fees`` by ticker).
        A closed market's check waits for its settlement; one whose fees are not known waits too,
        up to an hour (a gap in the index), then is kept unscored (its market has left the index).
        Returns how many were kept. Pages through in order of when each was made, so checks that
        wait never hold back the rest (merge review 3)."""
        done: list[tuple[int | None, int, str, int]] = []
        after = (-1, -1)
        for _ in range(SCORE_PAGES):
            rows = self._db.execute(
                "SELECT m.id, m.horizon_s, m.yes_bid_e6, m.yes_ask_e6, m.closed, r.payload,"
                " s.yes_paid_e6, m.at_ns, m.rowid FROM mark m JOIN recommendation r ON r.id = m.id"
                " LEFT JOIN settlement s ON s.ticker = r.ticker"
                " WHERE m.scored IS NULL AND NOT (m.closed = 1 AND s.ticker IS NULL)"
                " AND (m.at_ns, m.rowid) > (?, ?) ORDER BY m.at_ns, m.rowid LIMIT ?",
                (*after, limit),
            ).fetchall()
            for rid, horizon, bid, ask, closed, payload, paid, at, row in rows:
                after = (at, row)
                rec = _decode(payload)
                schedule = fees(rec.ticker)
                if schedule is None:  # not known now: tried again, or given up after an hour
                    # (from the start too: a check scored again after an upgrade is days old,
                    # and its market is looked up first: M1.R3 merge review 1)
                    if now_ns - max(at, waited_from) >= FEES_WAIT_NS:
                        done.append((None, 0, rid, horizon))
                    continue
                value, at_settlement = mark_score_e6(
                    schedule, rec.side, rec.count_e2, rec.entry_price_e6, bid, ask,
                    None if closed is None else bool(closed), paid,
                )  # fmt: skip
                done.append((value, int(at_settlement), rid, horizon))
            if len(rows) < limit or len(done) >= limit:
                break
        if done:
            self.changes += 1  # a shown check's score (merge review 6)
            with transaction(self._db):
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
        self.mark_all([(rid, horizon_s, at_ns, bid, ask, closed)])

    def mark_all(
        self, marks: list[tuple[str, int, int, int | None, int | None, bool | None]]
    ) -> None:
        """Each (id, horizon, at, bid, ask, closed) as a check, in one transaction (a backlog after
        a restart is thousands: M1.R3 C2)."""
        if not marks:
            return
        with transaction(self._db) as db:
            db.executemany(
                "INSERT OR IGNORE INTO mark (id, horizon_s, at_ns, yes_bid_e6, yes_ask_e6, closed)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                marks,
            )
            db.executemany(
                "DELETE FROM pending_mark WHERE id = ? AND horizon_s = ?",
                [(rid, horizon) for rid, horizon, *_ in marks],
            )
        self.changes += 1  # a shown recommendation's checks (merge review 6)

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
        if self._unsettled is None:
            self._unsettled = dict(
                self._db.execute(
                    "SELECT r.ticker, MAX(r.received_ns) FROM recommendation r LEFT JOIN settlement"
                    " s ON s.ticker = r.ticker WHERE s.ticker IS NULL GROUP BY r.ticker"
                ).fetchall()
            )
        old = [ticker for ticker, newest in self._unsettled.items() if newest < since_ns]
        for ticker in old:  # past the window for good: the callers' window only moves on
            del self._unsettled[ticker]
        return list(self._unsettled)

    def settle(self, ticker: str, result: str, yes_paid_e6: int, at_ns: int) -> None:
        """A market's settlement: its result and what a YES contract paid (kept once)."""
        self._db.execute(
            "INSERT OR IGNORE INTO settlement (ticker, result, at_ns, yes_paid_e6)"
            " VALUES (?, ?, ?, ?)",
            (ticker, result, at_ns, yes_paid_e6),
        )
        if self._unsettled is not None:
            self._unsettled.pop(ticker, None)

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
                rid,
                made,
                state,
                by,
                source,
                _decode(payload),
                marks.get(rid, []),
                settled,
                answer,
                paid,
            )  # fmt: skip
            for rid, made, state, by, source, payload, settled, paid, answer in rows
        ]


@dataclass
class JournalService:
    """Feeds the journal from the engine: ``listen`` is the engine's listener (it only queues);
    ``run`` writes, expires, marks and scores once a second, ranks the active ones on the board
    (offering the first to ``notices``) and works out the scorecard once a minute (M1.12c)."""

    journal: Journal
    quotes: Mapping[str, tuple[int | None, int | None]]  # the live YES bid and ask, by ticker
    now_ns: Callable[[], int]
    # a market's settlement, the market index's: its result (yes, no or scalar) and what a YES
    # contract paid (``MarketInfo.settlement``); None before
    result: Callable[[str], tuple[str, int] | None] = lambda ticker: None
    # whether a market still trades (True), has stopped (False), or is not known (None)
    trading: Callable[[str], bool | None] = lambda ticker: None
    # a market's fee schedule, by ticker (None: not known)
    fees: Callable[[str], FeeSchedule | None] = lambda ticker: None
    # markets to look up in the background (their fees: M1.R3 merge review 1)
    want: Callable[[list[str]], None] = lambda tickers: None
    notices: Notices | None = None
    board: Board = field(default_factory=Board)
    card: list[ScoreLine] = field(default_factory=list)
    card_at_ns: int = 0
    card_task: "asyncio.Task[None] | None" = None
    settled_at_ns: int = 0
    queue: asyncio.Queue[Item] = field(default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    dropped: int = 0
    rejected: int = 0
    recorded: int = 0
    failures: int = 0  # passes whose expiring or marking failed (said in health; retried next)
    started_ns: int | None = None  # the first pass: quotes come only from then (M1.R3 C2)

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
        self.journal.changes += 1  # a page built before this is built again (merge review 2)

    def _place(self, rid: str, rec: Recommendation, made_ns: int) -> bool:
        found = figures(rec, made_ns, self.fees(rec.ticker))
        return self.board.put(rid, rec, None if found is None else found.per_day_e6, made_ns)

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
                self.notices.offer(item.ref.id, rec)
        now = self.now_ns()
        if self.started_ns is None:
            self.started_ns = now
        self.journal.expire(now)
        self.board.prune(now)
        marks = []
        for rid, ticker, horizon, due in self.journal.due(now):
            bid, ask = self.quotes.get(ticker, (None, None))
            # no quote yet (a quiet market, or a fresh start: Kalshi sends a quote only when it
            # changes, so the wait runs from the start too: M1.R3 C2): marked when one comes
            if bid is None and ask is None and now - max(due, self.started_ns) < QUOTE_WAIT_NS:
                continue
            open_now = self.trading(ticker)
            marks.append((rid, horizon, now, bid, ask, None if open_now is None else not open_now))
        self.journal.mark_all(marks)
        if now - self.settled_at_ns >= SETTLE_EVERY_NS:  # once a minute, from the market index
            self.settled_at_ns = now
            for ticker in self.journal.unsettled(now - LONGEST_NS):
                found = self.result(ticker)
                if found is not None:
                    self.journal.settle(ticker, *found, now)
        missing: set[str] = set()

        def fees(ticker: str) -> FeeSchedule | None:
            found = self.fees(ticker)
            if found is None:
                missing.add(ticker)
            return found

        self.journal.score(fees, now, waited_from=self.started_ns)
        if missing:  # a market that left the index (closed days ago): looked up, a few at a time
            self.want(sorted(missing))

    def step(self) -> None:
        """One pass: what came is written, then expired, marked and scored (``run`` does this once
        a second; a replay once a virtual second: M1.13)."""
        items: list[Item] = []
        while not self.queue.empty():
            items.append(self.queue.get_nowait())
        self._write(items)

    async def refresh_card(self) -> None:
        """The scorecard, on a worker thread (its own read-only connection)."""
        now = self.now_ns()
        self.card_at_ns = now
        self.card = await in_daemon_thread(scorecard, self.journal.path, now - CARD_DAYS * DAY_NS)

    async def _card(self) -> None:
        try:
            await self.refresh_card()
        except Exception as exc:  # said in health; tried again in a minute
            self.failures += 1
            log.warning("scorecard failed", extra={"error": f"{type(exc).__name__}: {exc}"})

    async def run(self) -> None:
        try:
            self.load()
        except Exception as exc:  # the list starts empty and fills as recommendations come
            self.failures += 1
            log.warning("journal board not loaded", extra={"error": f"{type(exc).__name__}: {exc}"})
        try:
            while True:
                await asyncio.sleep(EVERY_S)
                running = self.card_task is not None and not self.card_task.done()
                if not running and self.now_ns() - self.card_at_ns >= SETTLE_EVERY_NS:
                    # beside the loop: a slow query never holds up writing and marking (review 4)
                    self.card_task = asyncio.create_task(self._card(), name="scorecard")
                try:
                    self.step()
                except Exception as exc:  # expiring or marking: never stops the engine; next second
                    self.failures += 1
                    log.warning("journal pass failed",
                                extra={"error": f"{type(exc).__name__}: {exc}"})  # fmt: skip
        finally:
            if self.card_task is not None:
                self.card_task.cancel()

    def flush(self) -> None:
        """At shutdown, once the plugins have stopped: what is still queued is written."""
        self.step()

    def health(self) -> dict[str, object]:
        return {"recorded": self.recorded, "rejected": self.rejected, "dropped": self.dropped,
                "failures": self.failures}  # fmt: skip
