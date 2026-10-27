r"""Backups to the NAS share (PLAN D18, G14): IKARS's own subtree, never anything else.

The share is the one KAPS backs its database up to; the owner chose it (2026-10-26). Its real path
lives only in the local settings (``backup.share``), never in the repository; the docs write
``\\<nas>\<share>`` and the tests use a folder of their own.

**The guard.** Every path is resolved under ``<share>\ikars\`` and anything outside is refused, so
nothing of KAPS's (its marker, pgBackRest's repository) can be read, listed or written. Nothing is
written unless ``ikars\.ikars-backup-marker`` exists: an unmounted, wrong or empty share is refused.
``init`` creates ``ikars\`` and the marker (owner's go-ahead, 2026-10-27), and nothing else.

**A copy** is made of a consistent snapshot (SQLite's online backup, into a local staging folder),
written to the share under a temporary name, read back and hash-checked, then renamed, so a copy on
the share is either whole or absent. Each folder gets a ``SHA256SUMS``.

**The tree** (dates are UTC)::

    ikars\ledger\daily\<yyyy>\<yyyy-mm-dd>\        ikars-ledger.sqlite, checkpoint.json, SHA256SUMS
    ikars\ledger\monthly\<yyyy>\<yyyy-mm>\         the same, rewritten daily (the month's last)
    ikars\ledger\checkpoints\<yyyy>\<date>.json     every local daily checkpoint
    ikars\ledger\pre-migration\                    the ledger as it was before each schema migration
    ikars\state\daily\<yyyy>\<yyyy-mm-dd>\         state.sqlite, SHA256SUMS
    ikars\config\settings.toml                      the settings (they never hold secrets)

**Retention:** ledger dailies 90 days, state dailies 30 days; monthlies, checkpoints and
pre-migration copies are kept for ever. The local ledger is never pruned.

**The restore drill** (G14) restores the newest daily ledger copy into a scratch file, checks its
hashes, verifies its chain, and checks that its head is in the local ledger with the same hash.
"""

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ikars_engine.ledger import Head, Ledger

MARKER = ".ikars-backup-marker"
SUBTREE = "ikars"
LEDGER_FILE = "ikars-ledger.sqlite"
STATE_FILE = "state.sqlite"
SUMS = "SHA256SUMS"
LEDGER_DAYS = 90
STATE_DAYS = 30
CHUNK = 1 << 20


class ShareError(RuntimeError):
    """The share cannot be used, or a path would leave IKARS's subtree."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def snapshot(source: Path, target: Path) -> Path:
    """A consistent copy of the SQLite file ``source`` (WAL included), by SQLite's online backup."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    reader = sqlite3.connect(source)  # it only reads; a read-only open can fail on a WAL file
    writer = sqlite3.connect(target)
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()
    return target


class Share:
    def __init__(self, share: str) -> None:
        if not share.strip():
            raise ShareError("no share is set (backup.share in the settings)")
        self.base = Path(share)
        self.root = self.base / SUBTREE

    # --- the guard ---------------------------------------------------------------------------

    def path(self, *parts: str) -> Path:
        """A path inside ``<share>/ikars/``; anything that would leave it is refused."""
        target = self.root.joinpath(*parts)
        inside = Path(os.path.normpath(target))
        if inside != Path(os.path.normpath(self.root)) and not inside.is_relative_to(
            Path(os.path.normpath(self.root))
        ):
            raise ShareError("a backup path would leave IKARS's subtree")
        return inside

    def problem(self) -> str | None:
        """Why the share cannot be written now, or None."""
        if not self.base.is_dir():
            return "the share is not reachable"
        if not (self.root / MARKER).is_file():
            return f"the share has no {SUBTREE}/{MARKER} (run: python -m ikars_engine backup init)"
        return None

    def init(self, now_ns: int) -> bool:
        """Create ``ikars/`` and its marker (and nothing else); False when both already existed."""
        if not self.base.is_dir():
            raise ShareError("the share is not reachable")
        marker = self.root / MARKER
        if marker.is_file():
            return False
        self.root.mkdir(exist_ok=True)
        stamp = datetime.fromtimestamp(now_ns // 1_000_000_000, UTC).isoformat()
        marker.write_text(
            f"IKARS backups (PLAN D18). IKARS writes only inside this folder. Created {stamp}.\n",
            encoding="utf-8",
        )
        return True

    def _ready(self) -> None:
        problem = self.problem()
        if problem is not None:
            raise ShareError(problem)

    # --- copies ------------------------------------------------------------------------------

    def put(self, local: Path, *parts: str) -> str:
        """Copy ``local`` to ``parts`` under the subtree: temporary name, hash check, rename.
        Returns the file's SHA-256."""
        self._ready()
        target = self.path(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        expected = sha256(local)
        shutil.copyfile(local, temporary)
        if sha256(temporary) != expected:
            temporary.unlink(missing_ok=True)
            raise ShareError("a copy on the share does not match its source")
        temporary.replace(target)
        return expected

    def put_folder(self, files: dict[str, Path], *parts: str) -> None:
        """Copy ``files`` (name -> local path) into one folder, with a ``SHA256SUMS``."""
        sums = [f"{self.put(local, *parts, name)}  {name}" for name, local in sorted(files.items())]
        listing = self.path(*parts, SUMS)
        temporary = listing.with_name(SUMS + ".tmp")
        temporary.write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
        temporary.replace(listing)

    def check_folder(self, folder: Path) -> bool:
        """Every file in ``SHA256SUMS`` is present with its hash."""
        listing = folder / SUMS
        if not listing.is_file():
            return False
        for line in listing.read_text(encoding="utf-8").splitlines():
            digest, _, name = line.partition("  ")
            if not (folder / name).is_file() or sha256(folder / name) != digest:
                return False
        return True

    # --- retention ---------------------------------------------------------------------------

    def prune(self, kind: str, keep_days: int, today: datetime) -> int:
        """Remove ``<kind>/daily`` folders older than ``keep_days``; returns how many."""
        removed = 0
        cutoff = (today - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        daily = self.path(kind, "daily")
        if not daily.is_dir():
            return 0
        for year in sorted(daily.iterdir()):
            for day in sorted(year.iterdir()) if year.is_dir() else []:
                if day.is_dir() and _is_date(day.name) and day.name < cutoff:
                    shutil.rmtree(self.path(kind, "daily", year.name, day.name))
                    removed += 1
        return removed

    def newest_daily(self, kind: str = "ledger") -> Path | None:
        daily = self.path(kind, "daily")
        days = (
            [d for d in daily.glob("*/*") if d.is_dir() and _is_date(d.name)]
            if daily.is_dir()
            else []
        )
        return max(days, key=lambda d: d.name) if days else None


def _is_date(name: str) -> bool:
    try:
        date.fromisoformat(name)
    except ValueError:
        return False
    return len(name) == 10


@dataclass(frozen=True, slots=True)
class BackupReport:
    day: str
    ledger_head: Head
    checkpoints: int
    pruned: int


def back_up(
    share: Share,
    *,
    ledger: Path,
    state: Path,
    settings: Path,
    checkpoints: Path,
    staging: Path,
    now_ns: int,
) -> BackupReport:
    """One backup of everything (safe to repeat the same day: the day's copies are replaced).
    Runs in a worker thread: it opens its own SQLite connections."""
    share._ready()
    moment = datetime.fromtimestamp(now_ns // 1_000_000_000, UTC)
    day, month, year = moment.strftime("%Y-%m-%d"), moment.strftime("%Y-%m"), moment.strftime("%Y")
    try:
        ledger_copy = snapshot(ledger, staging / LEDGER_FILE)
        copy = Ledger(ledger_copy, ZoneInfo("UTC"))
        try:
            head = copy.head()
            point = copy.checkpoint(staging / "point", now_ns)
        finally:
            copy.close()
        files = {LEDGER_FILE: ledger_copy, "checkpoint.json": point}
        share.put_folder(files, "ledger", "daily", year, day)
        share.put_folder(files, "ledger", "monthly", year, month)
        sent = 0
        for local in sorted(checkpoints.glob("*/*.json")):
            target = share.path("ledger", "checkpoints", local.parent.name, local.name)
            if not target.is_file() or sha256(target) != sha256(local):
                share.put(local, "ledger", "checkpoints", local.parent.name, local.name)
                sent += 1
        if state.is_file():
            state_copy = snapshot(state, staging / STATE_FILE)
            share.put_folder({STATE_FILE: state_copy}, "state", "daily", year, day)
        if settings.is_file():
            share.put(settings, "config", "settings.toml")
        pruned = share.prune("ledger", LEDGER_DAYS, moment) + share.prune(
            "state", STATE_DAYS, moment
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return BackupReport(day, head, sent, pruned)


def pre_migration(share: Share, copy: Path) -> None:
    """The ledger as it was before a schema migration, kept for ever (G: an update must never
    break the tax ledger)."""
    share.put(copy, "ledger", "pre-migration", copy.name)


@dataclass(frozen=True, slots=True)
class Drill:
    ok: bool
    day: str | None
    records: int
    problem: str | None = None


def restore_drill(share: Share, local: Ledger, scratch: Path) -> Drill:
    """Restore the newest daily ledger copy into ``scratch`` and prove it (G14)."""
    folder = share.newest_daily("ledger")
    if folder is None:
        return Drill(False, None, 0, "no ledger backup on the share")
    if not share.check_folder(folder):
        return Drill(False, folder.name, 0, "a backup file does not match its SHA256SUMS")
    scratch.mkdir(parents=True, exist_ok=True)
    restored = scratch / LEDGER_FILE
    shutil.copyfile(folder / LEDGER_FILE, restored)
    copy = Ledger(restored, ZoneInfo("UTC"))
    try:
        verified = copy.verify()
    finally:
        copy.close()
    if not verified.ok:
        return Drill(
            False, folder.name, verified.records, f"the restored chain: {verified.problem}"
        )
    point = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
    if (point["seq"], point["hash"]) != (verified.head.seq, verified.head.hash):
        return Drill(
            False, folder.name, verified.records, "the copy's head differs from its checkpoint"
        )
    if verified.head.seq and local.hash_at(verified.head.seq) != verified.head.hash:
        return Drill(
            False, folder.name, verified.records, "the copy's head is not in the local ledger"
        )
    return Drill(True, folder.name, verified.records)
