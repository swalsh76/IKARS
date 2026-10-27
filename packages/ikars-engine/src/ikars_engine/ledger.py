"""The audit and tax ledger (PLAN D18): one permanent, tamper-evident record of everything that
moved money in the Kalshi account, and of what IKARS did.

**Append-only.** A record is never changed or removed: SQLite triggers refuse both, and a correction
is a new record that names the one it corrects (``corrects``).

**Hash chain.** Each record's body is a canonical JSON text (sorted keys, no spaces, UTF-8;
integers, strings, booleans, null, lists and objects only: **never floats**, as all money is
fixed-point; Kalshi's own JSON rides along as one text field, verbatim).
The body holds the record's sequence number and the previous record's hash, and the record's hash
is the SHA-256 of its body, so changing, removing or reordering any record breaks every hash after
it. :meth:`Ledger.verify` checks the whole chain; it runs at every start and as
``python -m ikars_engine ledger verify``. Each day's head (UTC) is written to a checkpoint file.

**Streams.** ``real`` is the account; ``paper`` (D6) is a separate chain, clearly marked, that the
tax package never reads.

**Idempotent.** A record from Kalshi carries its source and Kalshi id, unique per stream, so an
import may run any number of times and adds each record once.

Nothing here logs a record's contents: they are account values (only counts and status are logged).
"""

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ikars_engine.store import open_store

GENESIS = "0" * 64
STREAMS = ("real", "paper")
KINDS = frozenset(
    {
        "fill",
        "settlement",
        "deposit",
        "withdrawal",
        "transfer",
        "fee",
        "funding",
        "interest",
        "incentive",
        "adjustment",
        "reconciliation",
        "app",
    }
)
APPEND_ONLY = "the ledger is append-only"

LEDGER_MIGRATIONS = (
    f"""
    CREATE TABLE record (
        stream TEXT NOT NULL CHECK (stream IN ('real', 'paper')),
        seq INTEGER NOT NULL CHECK (seq >= 1),
        kind TEXT NOT NULL,
        at_ns INTEGER NOT NULL,           -- when it happened (Kalshi's time), UTC nanoseconds
        source TEXT NOT NULL,             -- fill, settlement, deposit, ..., annotation, app
        source_id TEXT,                   -- Kalshi's id (or IKARS's key) for idempotence
        ticker TEXT,
        body TEXT NOT NULL,               -- the canonical JSON the hash covers
        hash TEXT NOT NULL,
        PRIMARY KEY (stream, seq)
    );
    CREATE UNIQUE INDEX record_source ON record (stream, source, source_id)
        WHERE source_id IS NOT NULL;
    CREATE INDEX record_kind ON record (stream, kind, at_ns);
    CREATE TRIGGER record_no_update BEFORE UPDATE ON record
    BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END;
    CREATE TRIGGER record_no_delete BEFORE DELETE ON record
    BEGIN SELECT RAISE(ABORT, '{APPEND_ONLY}'); END;
    CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)  -- bookmarks; not the record
    """,
)


class LedgerError(RuntimeError):
    pass


def canonical(value: Any) -> str:
    """The canonical JSON text of ``value``; a float anywhere is refused."""
    _no_floats(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _no_floats(value: Any, where: str = "") -> None:
    if isinstance(value, float):
        raise LedgerError(f"a float in the ledger ({where or 'value'}): money is fixed-point")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise LedgerError(f"a non-text key in the ledger ({where}{key!r})")
            _no_floats(item, f"{where}{key}.")
    elif isinstance(value, (list, tuple)):
        for item in value:
            _no_floats(item, where)
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise LedgerError(f"a {type(value).__name__} in the ledger ({where or 'value'})")


def raw_text(raw: Mapping[str, Any]) -> str:
    """Kalshi's JSON for a record, with sorted keys and no spaces (stable, so the hash is)."""
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def iso_utc(at_ns: int) -> str:
    seconds, ns = divmod(at_ns, 1_000_000_000)
    stamp = datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{stamp}.{ns:09d}Z"


def iso_local(at_ns: int, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(at_ns // 1_000_000_000, zone).isoformat()


@dataclass(frozen=True, slots=True)
class Entry:
    """A record to append; the ledger adds the sequence, the times, the chain and the hash.

    Money is in dollar millionths and counts in contract hundredths (integers). ``cash_e6`` is the
    record's effect on the account's cash, signed; ``raw`` is Kalshi's JSON for it, kept verbatim.
    """

    kind: str
    at_ns: int
    source: str
    source_id: str | None = None
    ticker: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    cash_e6: int | None = None
    raw: Mapping[str, Any] | None = None
    context: Mapping[str, Any] = field(default_factory=dict)
    note: str | None = None
    corrects: int | None = None


@dataclass(frozen=True, slots=True)
class Head:
    seq: int
    hash: str


@dataclass(frozen=True, slots=True)
class Verified:
    ok: bool
    records: int
    head: Head
    problem: str | None = None  # the first break, by sequence number (never record contents)


class Ledger:
    def __init__(
        self, path: Path, zone: ZoneInfo, on_backup: Callable[[Path], None] | None = None
    ) -> None:
        self.path = path
        self.zone = zone
        self._db = open_store(path, LEDGER_MIGRATIONS, on_backup)

    def close(self) -> None:
        self._db.close()

    # --- appending ---------------------------------------------------------------------------

    def append(self, entry: Entry, stream: str = "real") -> int | None:
        """Append one record; its sequence number, or None when its source id is already in."""
        added = self.append_all([entry], stream)
        return added[0] if added else None

    def append_all(self, entries: Sequence[Entry], stream: str = "real") -> list[int]:
        """Append records in order, in one transaction; the sequence numbers of those added
        (an entry whose source id is already in the stream is skipped)."""
        if stream not in STREAMS:
            raise LedgerError(f"no such stream {stream!r}")
        added: list[int] = []
        self._db.execute("BEGIN IMMEDIATE")
        try:
            head = self._head(stream)
            seq, prev = head.seq, head.hash
            for entry in entries:
                if entry.kind not in KINDS:
                    raise LedgerError(f"no such record kind {entry.kind!r}")
                if entry.source_id is not None and self._has(stream, entry.source, entry.source_id):
                    continue
                seq += 1
                body = canonical(self._body(stream, seq, prev, entry))
                prev = digest(body)
                self._db.execute(
                    "INSERT INTO record (stream, seq, kind, at_ns, source, source_id, ticker, body,"
                    " hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (stream, seq, entry.kind, entry.at_ns, entry.source, entry.source_id,
                     entry.ticker, body, prev),
                )  # fmt: skip
                added.append(seq)
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        return added

    def _body(self, stream: str, seq: int, prev: str, entry: Entry) -> dict[str, Any]:
        return {
            "stream": stream,
            "seq": seq,
            "prev_hash": prev,
            "kind": entry.kind,
            "at_ns": entry.at_ns,
            "time_utc": iso_utc(entry.at_ns),
            "time_local": iso_local(entry.at_ns, self.zone),
            "tax_zone": self.zone.key,
            "source": entry.source,
            "source_id": entry.source_id,
            "ticker": entry.ticker,
            "fields": dict(entry.fields),
            "cash_e6": entry.cash_e6,
            # Kalshi's JSON as text, kept as evidence (its own numbers may be anything)
            "raw": None if entry.raw is None else raw_text(entry.raw),
            "context": dict(entry.context),
            "note": entry.note,
            "corrects": entry.corrects,
        }

    def _has(self, stream: str, source: str, source_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM record WHERE stream = ? AND source = ? AND source_id = ?",
            (stream, source, source_id),
        ).fetchone()
        return row is not None

    def has(self, source: str, source_id: str, stream: str = "real") -> bool:
        return self._has(stream, source, source_id)

    # --- reading -----------------------------------------------------------------------------

    def _head(self, stream: str) -> Head:
        row = self._db.execute(
            "SELECT seq, hash FROM record WHERE stream = ? ORDER BY seq DESC LIMIT 1", (stream,)
        ).fetchone()
        return Head(0, GENESIS) if row is None else Head(int(row[0]), str(row[1]))

    def head(self, stream: str = "real") -> Head:
        return self._head(stream)

    def count(self, stream: str = "real", kind: str | None = None) -> int:
        query, args = "SELECT COUNT(*) FROM record WHERE stream = ?", [stream]
        if kind is not None:
            query, args = query + " AND kind = ?", [stream, kind]
        return int(self._db.execute(query, args).fetchone()[0])

    def records(self, stream: str = "real", kind: str | None = None) -> Iterator[dict[str, Any]]:
        """Each record's body, in sequence order."""
        query, args = "SELECT body FROM record WHERE stream = ?", [stream]
        if kind is not None:
            query, args = query + " AND kind = ?", [stream, kind]
        for (body,) in self._db.execute(query + " ORDER BY seq", args):
            yield json.loads(body)

    def page(
        self, before: int | None, limit: int, stream: str = "real", ticker: str | None = None
    ) -> list[dict[str, Any]]:
        """Up to ``limit`` record bodies with a sequence below ``before`` (all when None), newest
        first, of one market when ``ticker`` is given: the API's ledger pages."""
        query = "SELECT body FROM record WHERE stream = ?"
        params: list[str | int] = [stream]
        if before is not None:
            query += " AND seq < ?"
            params.append(before)
        if ticker is not None:
            query += " AND ticker = ?"
            params.append(ticker)
        rows = self._db.execute(query + " ORDER BY seq DESC LIMIT ?", [*params, limit])
        return [json.loads(body) for (body,) in rows]

    def hash_at(self, seq: int, stream: str = "real") -> str | None:
        row = self._db.execute(
            "SELECT hash FROM record WHERE stream = ? AND seq = ?", (stream, seq)
        ).fetchone()
        return None if row is None else str(row[0])

    def latest_at_ns(self, source: str, stream: str = "real") -> int | None:
        row = self._db.execute(
            "SELECT MAX(at_ns) FROM record WHERE stream = ? AND source = ?", (stream, source)
        ).fetchone()
        return None if row[0] is None else int(row[0])

    def last(self, kind: str, stream: str = "real") -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT body FROM record WHERE stream = ? AND kind = ? ORDER BY seq DESC LIMIT 1",
            (stream, kind),
        ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    # --- checking ----------------------------------------------------------------------------

    def verify(self, stream: str = "real") -> Verified:
        """Walk the whole chain: each body's hash, its link to the one before, consecutive
        sequence numbers, and the indexed columns against the body."""
        prev, expected, count = GENESIS, 1, 0
        rows = self._db.execute(
            "SELECT seq, kind, at_ns, source, source_id, ticker, body, hash FROM record"
            " WHERE stream = ? ORDER BY seq",
            (stream,),
        )
        for seq, kind, at_ns, source, source_id, ticker, body, stored in rows:
            problem = None
            try:
                data = json.loads(body)
                canonical_again = canonical(data) if isinstance(data, dict) else None
            except (ValueError, LedgerError):  # not JSON, or a float or odd key slipped in
                data = canonical_again = None
            if seq != expected:
                problem = f"sequence {expected} is missing (found {seq})"
            elif data is None or canonical_again != body:
                problem = f"record {seq} is not canonical"
            elif digest(body) != stored:
                problem = f"record {seq} does not match its hash"
            elif data.get("prev_hash") != prev:
                problem = f"record {seq} does not follow record {seq - 1}"
            elif (data.get("seq"), data.get("stream"), data.get("kind"), data.get("at_ns"),
                  data.get("source"), data.get("source_id"), data.get("ticker")) != (
                      seq, stream, kind, at_ns, source, source_id, ticker):  # fmt: skip
                problem = f"record {seq}'s columns differ from its body"
            if problem is not None:
                return Verified(False, count, Head(expected - 1, prev), problem)
            prev, expected, count = stored, expected + 1, count + 1
        return Verified(True, count, Head(expected - 1, prev))

    def against_checkpoint(self, folder: Path, stream: str = "real") -> str | None:
        """None when the newest checkpoint's record is still in the chain with the same hash;
        otherwise what is wrong. This catches what :meth:`verify` cannot: newest records cut off,
        or an older copy of the file put back."""
        for path in sorted(folder.glob("*/*.json"), reverse=True):
            point = json.loads(path.read_text(encoding="utf-8"))
            if point.get("stream") != stream:
                continue
            seq, day = int(point["seq"]), point["date_utc"]
            if seq == 0:
                return None
            stored = self.hash_at(seq, stream)
            if stored is None:
                return f"record {seq}, named by the checkpoint of {day}, is missing"
            if stored != point["hash"]:
                return f"record {seq} differs from the checkpoint of {day}"
            return None
        return None

    def checkpoint(self, folder: Path, now_ns: int, stream: str = "real") -> Path:
        """Write today's (UTC) head to ``folder/<yyyy>/<yyyy-mm-dd>.json``, atomically; the file
        is rewritten during the day, so the last write of a day holds that day's last hash."""
        head = self._head(stream)
        day = datetime.fromtimestamp(now_ns // 1_000_000_000, UTC).strftime("%Y-%m-%d")
        target = folder / day[:4] / f"{day}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        text = canonical(
            {"date_utc": day, "stream": stream, "seq": head.seq, "hash": head.hash,
             "written_utc": iso_utc(now_ns)}
        )  # fmt: skip
        temporary = target.with_suffix(".json.tmp")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
        return target

    # --- bookmarks (outside the chain) -------------------------------------------------------

    def bookmark(self, name: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return None if row is None else str(row[0])

    def set_bookmark(self, name: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta (name, value) VALUES (?, ?)"
            " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
            (name, value),
        )
