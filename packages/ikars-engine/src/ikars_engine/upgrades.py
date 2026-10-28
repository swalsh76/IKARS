"""Safe upgrades and the rollback (M1.9b, PLAN D20 G; owner, 2026-10-27: a pre-upgrade snapshot).

**Before an upgrade.** The first time a new version of the installed program starts, before
anything migrates, the data that versions change is copied whole to
``backups\\pre-upgrade\\<the old version>\\``: the settings, the engine's state, the ledger and its
checkpoints (the ledger's copy goes to the share as well), with a manifest. Each migration's own
backup still happens; this one is the set a rollback restores as one.

**The rollback.** ``ikars rollback <version>`` stops the engine, sets the current data aside in
``backups\\rolled-back\\`` (kept, never deleted), restores that version's snapshot, and has Velopack
install that release from GitHub. The older engine then starts on its own data and imports from
Kalshi what was recorded since; the owner's annotations made since are counted from the copy set
aside, to be entered again.
"""

import json
import logging
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ikars_engine.files import write_text_atomic
from ikars_engine.paths import Paths

log = logging.getLogger("ikars_engine.upgrades")
VERSION_FILE = "program.version"  # in the data folder: the version that last started
UNVERSIONED = "unversioned"  # data from before the first installed version (a development engine)
SNAPSHOTS_KEPT = 5
MANIFEST = "manifest.json"


class RollbackRefused(RuntimeError):
    """A rollback that cannot be done (said in plain words)."""


def _databases(paths: Paths) -> dict[str, Path]:
    return {
        "state.sqlite": paths.data / "state.sqlite",
        "ledger.sqlite": paths.ledger / "ledger.sqlite",
    }


def _copy_database(source: Path, target: Path) -> None:
    """A consistent copy, even of a database another process has open (SQLite's backup)."""
    with (
        closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as db,
        closing(sqlite3.connect(target)) as copy,
    ):
        db.backup(copy)


def _ledger_head(ledger: Path) -> int:
    if not ledger.is_file():
        return 0
    try:
        with closing(sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True)) as db:
            row = db.execute("SELECT MAX(seq) FROM record WHERE stream = 'real'").fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0)


def last_version(paths: Paths) -> str | None:
    marker = paths.data / VERSION_FILE
    return marker.read_text(encoding="utf-8").strip() or None if marker.is_file() else None


def snapshot(paths: Paths, previous: str, to: str, share: Any = None,
             now: datetime | None = None) -> Path:  # fmt: skip
    """Copy the data versions change to ``backups\\pre-upgrade\\<previous>``; returns the folder."""
    at = now or datetime.now(UTC)
    folder = paths.backups / "pre-upgrade" / previous
    if folder.exists():  # the same version again (a reinstall): never over an earlier snapshot
        folder = folder.with_name(f"{previous}-{at:%Y%m%dT%H%M%SZ}")
    folder.mkdir(parents=True)
    copied: list[str] = []
    if paths.settings.is_file():
        shutil.copy2(paths.settings, folder / paths.settings.name)
        copied.append(paths.settings.name)
    for name, source in _databases(paths).items():
        if source.is_file():
            _copy_database(source, folder / name)
            copied.append(name)
    checkpoints = paths.ledger / "checkpoints"
    if checkpoints.is_dir():
        shutil.copytree(checkpoints, folder / "checkpoints")
        copied.append("checkpoints")
    head = _ledger_head(paths.ledger / "ledger.sqlite")
    manifest = {"from": previous, "to": to, "at_utc": at.isoformat(timespec="seconds"),
                "files": copied, "ledger_head": head}  # fmt: skip
    write_text_atomic(folder / MANIFEST, json.dumps(manifest, indent=2))
    if share is not None and (folder / "ledger.sqlite").is_file():
        share(folder / "ledger.sqlite")  # the ledger's copy to the share too, as a migration does
    _prune(paths.backups / "pre-upgrade")
    return folder


def _prune(folder: Path) -> None:
    kept = sorted((p for p in folder.iterdir() if (p / MANIFEST).is_file()),
                  key=lambda p: p.stat().st_mtime)  # fmt: skip
    for old in kept[:-SNAPSHOTS_KEPT]:
        shutil.rmtree(old)


def before_start(paths: Paths, version: str | None, share: Any = None) -> Path | None:
    """Called before the settings are first read: a snapshot when the program's version changed
    (or when data from before the installed program meets it). None: nothing to do."""
    if version is None:  # a development checkout: versions are not tracked
        return None
    previous = last_version(paths)
    has_data = paths.settings.is_file() or (paths.ledger / "ledger.sqlite").is_file()
    made = None
    if previous != version and (previous is not None or has_data):
        made = snapshot(paths, previous or UNVERSIONED, version, share)
        log.info("pre-upgrade snapshot", extra={"from": previous or UNVERSIONED, "to": version})
    if previous != version:
        paths.data.mkdir(parents=True, exist_ok=True)
        write_text_atomic(paths.data / VERSION_FILE, version + "\n")
    return made


def snapshots(paths: Paths) -> dict[str, dict[str, Any]]:
    """The pre-upgrade snapshots by folder name, with their manifests."""
    folder = paths.backups / "pre-upgrade"
    if not folder.is_dir():
        return {}
    return {p.name: json.loads((p / MANIFEST).read_text(encoding="utf-8"))
            for p in sorted(folder.iterdir()) if (p / MANIFEST).is_file()}  # fmt: skip


def annotations_since(ledger: Path, head: int) -> int:
    """The owner's annotations recorded after ``head`` (to be entered again after a rollback)."""
    if not ledger.is_file():
        return 0
    try:
        with closing(sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True)) as db:
            row = db.execute(
                "SELECT COUNT(*) FROM record WHERE seq > ? AND source = 'annotation'", (head,)
            ).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0])


def snapshot_of(paths: Paths, version: str) -> str | None:
    """The newest snapshot taken when leaving ``version`` (a reinstall adds a timestamped one)."""
    found = [(m["at_utc"], n) for n, m in snapshots(paths).items() if m.get("from") == version]
    return max(found)[1] if found else None


def _places(paths: Paths) -> dict[str, Path]:
    return {paths.settings.name: paths.settings, **_databases(paths),
            "checkpoints": paths.ledger / "checkpoints"}  # fmt: skip


def restore(
    paths: Paths, version: str, current: str, now: datetime | None = None
) -> dict[str, Any]:
    """Set the current data aside and put back the snapshot taken when leaving ``version`` (the
    engine must be stopped). A failure part way puts the current data back as it was."""
    name = snapshot_of(paths, version)
    if name is None:
        listed = ", ".join(sorted({m["from"] for m in snapshots(paths).values()})) or "none"
        raise RollbackRefused(f"no pre-upgrade snapshot of {version} (there are: {listed})")
    manifest = snapshots(paths)[name]
    at = now or datetime.now(UTC)
    source = paths.backups / "pre-upgrade" / name
    aside = paths.backups / "rolled-back" / f"{current}-{at:%Y%m%dT%H%M%SZ}"
    aside.mkdir(parents=True)
    places = _places(paths)
    moved: list[tuple[Path, Path]] = []
    try:
        for name_in, place in places.items():
            for extra in ("", "-wal", "-shm"):  # a database's journal goes with it
                live = place.with_name(place.name + extra)
                if live.exists():
                    shutil.move(live, aside / (name_in + extra))
                    moved.append((aside / (name_in + extra), live))
        for name_in in manifest["files"]:
            target = places[name_in]
            target.parent.mkdir(parents=True, exist_ok=True)
            copy = source / name_in
            shutil.copytree(copy, target) if copy.is_dir() else shutil.copy2(copy, target)
    except Exception as exc:  # undo: the current data back where it was, nothing half restored
        for place in places.values():
            if place.is_dir():
                shutil.rmtree(place)
            elif place.exists():
                place.unlink()
        for kept, live in moved:
            shutil.move(kept, live)
        problem = f"the snapshot could not be restored ({exc}); nothing changed"
        raise RollbackRefused(problem) from exc
    write_text_atomic(paths.data / VERSION_FILE, version + "\n")
    since = annotations_since(aside / "ledger.sqlite", int(manifest["ledger_head"]))
    return {"restored": name, "set_aside": str(aside.name), "annotations_to_enter_again": since}
