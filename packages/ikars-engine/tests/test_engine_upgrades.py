"""M1.9b: the pre-upgrade snapshot and the rollback (made-up data; Velopack is a fake here)."""

import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from ikars_engine import launcher, upgrades
from ikars_engine.paths import Paths
from ikars_engine.runmode_cli import rollback
from ikars_engine.upgrades import RollbackRefused

AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def ledger(path: Path, rows: list[tuple[int, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS record (stream TEXT, seq INTEGER, source TEXT)")
        db.executemany("INSERT INTO record VALUES ('real', ?, ?)", rows)


def heads(path: Path) -> int:
    with closing(sqlite3.connect(path)) as db:
        return int(db.execute("SELECT MAX(seq) FROM record").fetchone()[0] or 0)


def engine_data(tmp_path: Path) -> Paths:
    paths = Paths(tmp_path / "IKARS").create()
    paths.settings.write_text("version = 5\n", encoding="utf-8")
    ledger(paths.ledger / "ledger.sqlite", [(1, "fill"), (2, "annotation")])
    with closing(sqlite3.connect(paths.data / "state.sqlite")) as db, db:
        db.execute("CREATE TABLE gap (id INTEGER)")
    (paths.ledger / "checkpoints").mkdir()
    (paths.ledger / "checkpoints" / "real.json").write_text('{"seq": 2}', encoding="utf-8")
    return paths


def test_a_development_checkout_takes_no_snapshot(tmp_path: Path) -> None:
    paths = engine_data(tmp_path)
    assert upgrades.before_start(paths, None) is None
    assert not (paths.data / upgrades.VERSION_FILE).exists()


def test_a_fresh_install_takes_none_and_development_data_is_kept_as_unversioned(
    tmp_path: Path,
) -> None:
    fresh = Paths(tmp_path / "fresh")
    assert upgrades.before_start(fresh, "0.1.0") is None
    assert upgrades.last_version(fresh) == "0.1.0"
    paths = engine_data(tmp_path)  # a development engine's data meets the installed program
    made = upgrades.before_start(paths, "0.1.0")
    assert made is not None and made.name == upgrades.UNVERSIONED
    assert upgrades.before_start(paths, "0.1.0") is None  # the same version: nothing again


def test_an_upgrade_copies_settings_state_ledger_and_checkpoints_first(tmp_path: Path) -> None:
    paths = engine_data(tmp_path)
    upgrades.before_start(paths, "0.1.0")
    sent: list[Path] = []
    made = upgrades.before_start(paths, "0.1.1", share=sent.append)
    assert made is not None and made.name == "0.1.0"
    manifest = json.loads((made / upgrades.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["from"] == "0.1.0" and manifest["to"] == "0.1.1"
    assert set(manifest["files"]) == {
        "settings.toml",
        "state.sqlite",
        "ledger.sqlite",
        "checkpoints",
    }
    assert manifest["ledger_head"] == 2 and heads(made / "ledger.sqlite") == 2
    assert (made / "checkpoints" / "real.json").is_file()
    assert sent == [made / "ledger.sqlite"]  # the ledger's copy to the share too
    assert paths.settings.read_text(encoding="utf-8") == "version = 5\n"  # nothing changed
    assert upgrades.last_version(paths) == "0.1.1"


def test_snapshots_are_kept_to_the_latest_five_and_never_overwritten(tmp_path: Path) -> None:
    paths = engine_data(tmp_path)
    for n in range(7):
        upgrades.snapshot(paths, f"0.1.{n}", f"0.1.{n + 1}", now=AT + timedelta(minutes=n))
    again = upgrades.snapshot(paths, "0.1.6", "0.1.7", now=AT + timedelta(hours=1))
    assert again.name == "0.1.6-20261027T130000Z"
    assert len(upgrades.snapshots(paths)) == upgrades.SNAPSHOTS_KEPT


def test_a_restore_sets_the_current_data_aside_and_counts_annotations_since(
    tmp_path: Path,
) -> None:
    paths = engine_data(tmp_path)
    upgrades.before_start(paths, "0.1.0")
    upgrades.before_start(paths, "0.1.1")  # the snapshot of 0.1.0: ledger head 2
    ledger(paths.ledger / "ledger.sqlite", [(3, "fill"), (4, "annotation"), (5, "annotation")])
    (paths.data / "state.sqlite-wal").write_bytes(b"journal")
    paths.settings.write_text("version = 6\n", encoding="utf-8")
    done = upgrades.restore(paths, "0.1.0", "0.1.1", now=AT)
    assert done["annotations_to_enter_again"] == 2
    assert paths.settings.read_text(encoding="utf-8") == "version = 5\n"
    assert heads(paths.ledger / "ledger.sqlite") == 2  # the snapshot's; Kalshi's re-import follows
    aside = paths.backups / "rolled-back" / done["set_aside"]
    assert heads(aside / "ledger.sqlite") == 5 and (aside / "state.sqlite-wal").is_file()
    assert not (paths.data / "state.sqlite-wal").exists()  # never a newer journal on an old file
    assert upgrades.last_version(paths) == "0.1.0"
    with pytest.raises(RollbackRefused, match="there are: 0.1.0"):
        upgrades.restore(paths, "0.0.9", "0.1.1")


# --- ikars rollback ------------------------------------------------------------------------------


@dataclass
class Release:
    Version: str


@dataclass
class Found:
    TargetFullRelease: Release


@dataclass
class Back:
    release: str | None
    steps: list[str]

    def check_for_updates(self) -> Found | None:
        return None if self.release is None else Found(Release(self.release))

    def download_updates(self, found: Found) -> None:
        self.steps.append("download")

    def wait_exit_then_apply_updates(self, found: Found, silent: bool, restart: bool) -> None:
        self.steps.append(f"apply {found.TargetFullRelease.Version} {silent} {restart}")


@dataclass
class Managers:
    """``ikars rollback``'s Velopack, faked: where it is installed, the current and the back one."""

    where: Any = "installed"
    installed: str = "0.1.1"
    release: str | None = "0.1.0"
    steps: list[str] = field(default_factory=list)

    def current(self, where: Any) -> Any:
        return type("Current", (), {"get_current_version": lambda s: self.installed})()

    def back(self, version: str, where: Any) -> Back:
        return Back(self.release, self.steps)


def test_a_rollback_downloads_first_then_stops_restores_and_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = engine_data(tmp_path)
    upgrades.before_start(paths, "0.1.0")
    upgrades.before_start(paths, "0.1.1")
    managers = Managers()
    monkeypatch.setattr(launcher, "stop_engine", lambda p: managers.steps.append("stop"))
    done = rollback(paths, "0.1.0", managers)
    assert managers.steps == ["download", "stop", "apply 0.1.0 True True"]
    assert done["rolled_back"] is True and done["installing"] == "0.1.0"
    assert upgrades.last_version(paths) == "0.1.0"


@pytest.mark.parametrize(
    ("managers", "version", "says"),
    [
        (Managers(where=None), "0.1.0", "only the installed program"),
        (Managers(), "0.1.1", "installed now"),
        (Managers(), "0.0.9", "no pre-upgrade snapshot"),
        (Managers(release=None), "0.1.0", "was not found"),
    ],
)
def test_a_rollback_that_cannot_be_done_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, managers: Managers, version: str, says: str
) -> None:
    paths = engine_data(tmp_path)
    upgrades.before_start(paths, "0.1.0")
    upgrades.before_start(paths, "0.1.1")
    monkeypatch.setattr(launcher, "stop_engine", lambda p: managers.steps.append("stop"))
    with pytest.raises(RollbackRefused, match=says):
        rollback(paths, version, managers)
    assert "stop" not in managers.steps and upgrades.last_version(paths) == "0.1.1"


def test_the_newest_snapshot_of_a_version_is_restored_and_a_failed_swap_is_undone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = engine_data(tmp_path)
    upgrades.snapshot(paths, "0.1.0", "0.1.1", now=AT)
    ledger(paths.ledger / "ledger.sqlite", [(3, "fill")])
    later = upgrades.snapshot(paths, "0.1.0", "0.1.1", now=AT + timedelta(hours=1))  # a reinstall
    assert upgrades.snapshot_of(paths, "0.1.0") == later.name
    ledger(paths.ledger / "ledger.sqlite", [(4, "fill")])
    real_copy = shutil.copy2

    def full_disk(source: Any, target: Any) -> Any:
        if str(target).endswith("ledger.sqlite"):
            raise OSError("the disk is full")
        return real_copy(source, target)

    monkeypatch.setattr(shutil, "copy2", full_disk)
    with pytest.raises(RollbackRefused, match="nothing changed"):
        upgrades.restore(paths, "0.1.0", "0.1.1", now=AT + timedelta(hours=2))
    assert heads(paths.ledger / "ledger.sqlite") == 4  # the current data, as it was
    assert paths.settings.is_file() and (paths.ledger / "checkpoints" / "real.json").is_file()
    monkeypatch.setattr(shutil, "copy2", real_copy)
    upgrades.restore(paths, "0.1.0", "0.1.1", now=AT + timedelta(hours=3))
    assert heads(paths.ledger / "ledger.sqlite") == 3  # the newer snapshot's
