r"""Backups to the NAS share (PLAN D18, G14): IKARS's own subtree, never anything else.

The share is the one KAPS backs its database up to; the owner chose it (2026-10-26). Its real path
lives only in the local settings (``backup.share``), never in the repository; the docs write
``\\<nas>\<share>`` and the tests use a folder of their own.

**The guard.** Every path is resolved under ``<share>\ikars\`` and anything outside is refused, so
nothing of KAPS's (its marker, pgBackRest's repository) can be read, listed or written. Nothing is
written unless ``ikars\.ikars-backup-marker`` exists: an unmounted, wrong or empty share is refused.
``init`` creates ``ikars\`` and the marker (owner's go-ahead, 2026-10-27), and nothing else.

**A copy** is made of a consistent snapshot (SQLite's online backup, into a staging folder of the
run's own), written to the share under a temporary name of its own, read back and hash-checked, then
renamed, so a copy on the share is either whole or absent. A dated folder (its files and its
``SHA256SUMS``) is built whole beside the old one and then swapped in, so a reader sees the old day
or the new one, never a mix. Two backups at once (the engine's and the command's) never share a
name; at worst one of them fails and says so.

**The tree** (dates are UTC)::

    ikars\ledger\daily\<yyyy>\<yyyy-mm-dd>\        ikars-ledger.sqlite, checkpoint.json, SHA256SUMS
    ikars\ledger\monthly\<yyyy>\<yyyy-mm>\         the same, rewritten daily (the month's last)
    ikars\ledger\checkpoints\<yyyy>\<date>.json     every local daily checkpoint
    ikars\ledger\pre-migration\                    the ledger as it was before each schema migration
    ikars\state\daily\<yyyy>\<yyyy-mm-dd>\         state.sqlite, journal.sqlite, SHA256SUMS
    ikars\config\settings.toml                      the settings (they never hold secrets)

**Retention:** ledger dailies 90 days, state dailies 30 days; monthlies, checkpoints and
pre-migration copies are kept for ever. The local ledger is never pruned.

**The restore drill** (G14) restores the newest daily ledger copy into a scratch file, checks its
hashes, verifies its chain, and checks that its head is in the local ledger with the same hash.

**The restore** (D20 I, the workstation-lost runbook, ``docs/RUNBOOK.md``) puts the newest backup
into an empty data root: the same proofs, then the checkpoints, state, journal and settings.
"""

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from ikars_engine.ledger import Head, Ledger
from ikars_engine.settings import SettingsError, read
from ikars_engine.store import StoreError

log = logging.getLogger("ikars_engine.backup")

MARKER = ".ikars-backup-marker"
SUBTREE = "ikars"
LEDGER_FILE = "ikars-ledger.sqlite"
STATE_FILE = "state.sqlite"
JOURNAL_FILE = "journal.sqlite"
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
        temporary = target.with_name(f"{target.name}.{uuid4().hex}.tmp")
        digest = _checked_copy(local, temporary)
        temporary.replace(target)
        return digest

    def put_folder(self, files: dict[str, Path], *parts: str) -> None:
        """Copy ``files`` (name -> local path) into one folder with a ``SHA256SUMS``: built whole
        under a temporary name, then swapped in for the old folder."""
        self._ready()
        final = self.path(*parts)
        final.parent.mkdir(parents=True, exist_ok=True)
        _sweep(final)
        building = final.with_name(f"{final.name}.{uuid4().hex}.new")
        building.mkdir()
        sums = [
            f"{_checked_copy(local, building / name)}  {name}"
            for name, local in sorted(files.items())
        ]
        (building / SUMS).write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
        old = None
        if final.exists():
            old = final.with_name(f"{final.name}.{uuid4().hex}.old")
            final.rename(old)
        building.rename(final)
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)

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


def _checked_copy(local: Path, target: Path) -> str:
    """Copy and read back: the SHA-256, or ShareError when the copy differs from its source."""
    expected = sha256(local)
    shutil.copyfile(local, target)
    if sha256(target) != expected:
        target.unlink(missing_ok=True)
        raise ShareError("a copy on the share does not match its source")
    return expected


STRAY_S = 3_600  # a half-built or half-replaced folder this old was left by a run that died


def _sweep(final: Path) -> None:
    """Remove what a run that died left beside ``final`` (never another run's work in progress)."""
    now = time.time()
    for stray in final.parent.glob(f"{final.name}.*"):
        if stray.suffix in (".new", ".old") and now - stray.stat().st_mtime > STRAY_S:
            shutil.rmtree(stray, ignore_errors=True)


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
    journal: Path | None = None,
) -> BackupReport:
    """One backup of everything (safe to repeat the same day: the day's copies are replaced).
    Runs in a worker thread: it opens its own SQLite connections."""
    share._ready()
    staging = staging / uuid4().hex  # the run's own: another run's files are never touched
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
        points = sorted(checkpoints.glob("*/*.json"))
        recent = set(points[-2:])  # yesterday's may have changed after the last backup
        for local in points:
            target = share.path("ledger", "checkpoints", local.parent.name, local.name)
            if local in recent or not target.is_file():
                share.put(local, "ledger", "checkpoints", local.parent.name, local.name)
                sent += 1
        if state.is_file():
            files = {STATE_FILE: snapshot(state, staging / STATE_FILE)}
            if journal is not None and journal.is_file():  # the recommendations (M1.12a)
                files[JOURNAL_FILE] = snapshot(journal, staging / JOURNAL_FILE)
            share.put_folder(files, "state", "daily", year, day)
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


def migration_copier(share: Share | None) -> Callable[[Path], None] | None:
    """For ``Ledger(on_backup=...)``: before a ledger migration, its local copy goes to the share
    too (G). A share that cannot be reached is logged; the local copy still stands."""
    if share is None:
        return None

    def copy(path: Path) -> None:
        try:
            pre_migration(share, path)
        except (OSError, ShareError) as exc:
            problem = {"error": type(exc).__name__}
            log.warning("pre-migration copy not sent to the share", extra=problem)

    return copy


def configured(share: str) -> Share | None:
    """The share the settings name, or None when none is set."""
    return Share(share) if share.strip() else None


@dataclass(frozen=True, slots=True)
class Drill:
    ok: bool
    day: str | None
    records: int
    problem: str | None = None


def restore_drill(share: Share, local: Ledger, scratch: Path) -> Drill:
    """Restore the newest daily ledger copy into ``scratch`` and prove it (G14)."""
    problem = share.problem()
    if problem is not None:
        return Drill(False, None, 0, problem)
    try:
        return _drill(share, local, scratch)
    except OSError as exc:  # the share went away part-way
        return Drill(False, None, 0, f"the share could not be read ({type(exc).__name__})")
    except (sqlite3.DatabaseError, StoreError, ValueError, KeyError) as exc:
        # a copy that matches its checksums but is not a sound ledger (M1.R 22)
        return Drill(
            False, None, 0, f"the restored copy is not a sound ledger ({type(exc).__name__})"
        )


def _drill(share: Share, local: Ledger, scratch: Path) -> Drill:
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


@dataclass(frozen=True, slots=True)
class Restored:
    ledger_day: str
    records: int
    checkpoints: int
    state_day: str | None  # None: no state backup on the share yet
    journal: bool
    settings: bool


def restore(
    share: Share,
    *,
    ledger: Path,
    checkpoints: Path,
    state: Path,
    journal: Path,
    settings: Path,
) -> Restored:
    """The newest backup into a data root that holds no IKARS data (a lost workstation, PLAN
    D20 I): the ledger with its chain and checkpoint proved, its checkpoints, the engine state, the
    journal and the settings. Everything is checked in a staging folder first and only then moved
    into place, so a failed restore leaves the root as empty as it was. The share is only read."""
    problem = share.problem()
    if problem is not None:
        raise ShareError(problem)
    if any(p.exists() for p in (ledger, state, journal, settings)) or (
        checkpoints.is_dir() and any(checkpoints.iterdir())
    ):
        raise ShareError("the data folder already holds IKARS data: restore only into an empty one")
    folder = share.newest_daily("ledger")
    if folder is None:
        raise ShareError("no ledger backup on the share")
    if not share.check_folder(folder):
        raise ShareError("a ledger backup file does not match its SHA256SUMS")
    staging = ledger.parent / f".restore-{uuid4().hex}"
    staging.mkdir(parents=True)
    try:
        staged = staging / LEDGER_FILE
        shutil.copyfile(folder / LEDGER_FILE, staged)
        copy = Ledger(staged, ZoneInfo("UTC"))
        try:
            verified = copy.verify()
        finally:
            copy.close()
        if not verified.ok:
            raise ShareError(f"the backup's chain: {verified.problem}")
        point = json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))
        if (point["seq"], point["hash"]) != (verified.head.seq, verified.head.hash):
            raise ShareError("the backup's head differs from its checkpoint")
        moves = [(staged, ledger)]
        points = sorted(share.path("ledger", "checkpoints").glob("*/*.json"))
        for source in points:
            target = staging / "checkpoints" / source.parent.name / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        if points:
            moves.append((staging / "checkpoints", checkpoints))
        state_folder = share.newest_daily("state")
        has_journal = False
        if state_folder is not None:
            if not share.check_folder(state_folder):
                raise ShareError("a state backup file does not match its SHA256SUMS")
            shutil.copyfile(state_folder / STATE_FILE, staging / STATE_FILE)
            moves.append((staging / STATE_FILE, state))
            if (state_folder / JOURNAL_FILE).is_file():
                shutil.copyfile(state_folder / JOURNAL_FILE, staging / JOURNAL_FILE)
                moves.append((staging / JOURNAL_FILE, journal))
                has_journal = True
        saved = share.path("config", "settings.toml")
        if saved.is_file():
            shutil.copyfile(saved, staging / "settings.toml")
            try:
                read(staging / "settings.toml")
            except SettingsError as exc:
                raise ShareError(f"the saved settings cannot be read: {exc}") from exc
            moves.append((staging / "settings.toml", settings))
        if checkpoints.is_dir():  # empty (checked above): a folder cannot be replaced onto it
            checkpoints.rmdir()
        moved: list[Path] = []
        try:
            for source, target in moves:
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
                moved.append(target)
        except OSError:  # part-way: what moved goes again, so the root is as empty as it was
            for target in moved:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return Restored(
        folder.name,
        verified.records,
        len(points),
        None if state_folder is None else state_folder.name,
        has_journal,
        saved.is_file(),
    )
