"""SQLite stores with versioned migrations (PLAN D4 C, D20 G), and the engine's state store.

Every SQLite file IKARS keeps (the state store here, the ledger in M1.5) is opened through
:func:`open_store`: WAL mode, ``synchronous=FULL`` (a committed write survives a power cut), foreign
keys on. Its schema is a numbered list of migrations; the file's ``user_version`` says how far it
has come. Before any migration runs, the whole file is copied with SQLite's online backup to a name
that never collides, so an upgrade can always be undone, and every step runs in a transaction of
its own.

The **state store** holds the engine's small, durable facts: the gap log (what the feed may have
missed, and what became of it), bookmarks (discovery's), heartbeats (the last frame seen per
channel, so a crash becomes a gap and not silence) and the exchange's status history.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class StoreError(RuntimeError):
    pass


def backup_store(connection: sqlite3.Connection, path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.stem}.{stamp}.bak{path.suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.stem}.{stamp}-{n}.bak{path.suffix}")
        n += 1
    copy = sqlite3.connect(target)
    try:
        connection.backup(copy)
    finally:
        copy.close()
    return target


def open_store(path: Path, migrations: Sequence[str]) -> sqlite3.Connection:
    """Open (or create) ``path`` and bring it to ``len(migrations)``, backing it up first when an
    existing file must change. A file from a newer IKARS is refused."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists() and path.stat().st_size > 0
    connection = sqlite3.connect(path, isolation_level=None)  # transactions are explicit
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version > len(migrations):
        connection.close()
        raise StoreError(
            f"{path.name} is from a newer IKARS (schema {version} > {len(migrations)})"
        )
    if existed and version < len(migrations):
        backup_store(connection, path)
    for number in range(version, len(migrations)):
        connection.execute("BEGIN")
        try:
            for statement in _statements(migrations[number]):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {number + 1}")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            connection.close()
            raise
    return connection


def _statements(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


STATE_MIGRATIONS = (
    """
    CREATE TABLE gap (
        id INTEGER PRIMARY KEY,
        channel TEXT NOT NULL,          -- trade, ticker, lifecycle, book, or a plugin's name
        reason TEXT NOT NULL,           -- reconnect, restart, sequence, overflow, ...
        start_ns INTEGER NOT NULL,      -- the last frame actually received before the loss
        end_ns INTEGER NOT NULL,
        recovery TEXT NOT NULL,         -- backfill, discovery, unrecoverable
        status TEXT NOT NULL,           -- open, done, failed, unrecoverable
        detail TEXT NOT NULL DEFAULT '',
        recorded_ns INTEGER NOT NULL
    );
    CREATE INDEX gap_open ON gap (status) WHERE status = 'open';
    CREATE TABLE bookmark (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
    CREATE TABLE heartbeat (channel TEXT PRIMARY KEY, at_ns INTEGER NOT NULL);
    CREATE TABLE exchange_status (
        at_ns INTEGER NOT NULL,
        exchange_active INTEGER NOT NULL,
        trading_active INTEGER NOT NULL,
        detail TEXT NOT NULL
    );
    CREATE TABLE clean_shutdown (id INTEGER PRIMARY KEY CHECK (id = 1), at_ns INTEGER NOT NULL)
    """,
)


@dataclass(frozen=True, slots=True)
class Gap:
    id: int
    channel: str
    reason: str
    start_ns: int
    end_ns: int
    recovery: str
    status: str
    detail: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db = open_store(path, STATE_MIGRATIONS)

    def close(self) -> None:
        self._db.close()

    # --- gaps --------------------------------------------------------------------------------

    def add_gap(
        self,
        channel: str,
        reason: str,
        start_ns: int,
        end_ns: int,
        recovery: str,
        recorded_ns: int,
        detail: str = "",
    ) -> int:
        status = "unrecoverable" if recovery == "unrecoverable" else "open"
        cursor = self._db.execute(
            "INSERT INTO gap (channel, reason, start_ns, end_ns, recovery, status, detail,"
            " recorded_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (channel, reason, start_ns, end_ns, recovery, status, detail, recorded_ns),
        )
        return int(cursor.lastrowid or 0)

    def finish_gap(self, gap_id: int, status: str, detail: str) -> None:
        if status not in ("done", "failed"):
            raise StoreError(f"a gap finishes done or failed, not {status!r}")
        self._db.execute(
            "UPDATE gap SET status = ?, detail = ? WHERE id = ?", (status, detail, gap_id)
        )

    def gaps(self, status: str | None = None) -> list[Gap]:
        query = "SELECT id, channel, reason, start_ns, end_ns, recovery, status, detail FROM gap"
        rows = (
            self._db.execute(query + " WHERE status = ? ORDER BY id", (status,))
            if status
            else self._db.execute(query + " ORDER BY id")
        )
        return [Gap(*row) for row in rows]

    # --- bookmarks and heartbeats ------------------------------------------------------------

    def bookmark(self, name: str) -> int | None:
        row = self._db.execute("SELECT value FROM bookmark WHERE name = ?", (name,)).fetchone()
        return None if row is None else int(row[0])

    def set_bookmark(self, name: str, value: int) -> None:
        self._db.execute(
            "INSERT INTO bookmark (name, value) VALUES (?, ?)"
            " ON CONFLICT (name) DO UPDATE SET value = excluded.value",
            (name, value),
        )

    def beat(self, beats: dict[str, int]) -> None:
        self._db.execute("BEGIN")
        self._db.executemany(
            "INSERT INTO heartbeat (channel, at_ns) VALUES (?, ?)"
            " ON CONFLICT (channel) DO UPDATE SET at_ns = excluded.at_ns",
            list(beats.items()),
        )
        self._db.execute("DELETE FROM clean_shutdown")
        self._db.execute("COMMIT")

    def heartbeats(self) -> dict[str, int]:
        return dict(self._db.execute("SELECT channel, at_ns FROM heartbeat").fetchall())

    def mark_clean_shutdown(self, at_ns: int) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO clean_shutdown (id, at_ns) VALUES (1, ?)", (at_ns,)
        )

    def clear_clean_shutdown(self) -> None:
        """Running again: a crash from here on is not a clean shutdown."""
        self._db.execute("DELETE FROM clean_shutdown")

    def shut_down_cleanly(self) -> bool:
        return self._db.execute("SELECT 1 FROM clean_shutdown").fetchone() is not None

    # --- the exchange ------------------------------------------------------------------------

    def last_exchange_status(self) -> tuple[bool, bool, str] | None:
        row = self._db.execute(
            "SELECT exchange_active, trading_active, detail FROM exchange_status"
            " ORDER BY at_ns DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return None if row is None else (bool(row[0]), bool(row[1]), str(row[2]))

    def add_exchange_status(self, at_ns: int, exchange: bool, trading: bool, detail: str) -> None:
        self._db.execute(
            "INSERT INTO exchange_status VALUES (?, ?, ?, ?)",
            (at_ns, int(exchange), int(trading), detail),
        )
