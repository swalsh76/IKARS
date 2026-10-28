"""M1.9a: where the installed and portable program keep their data, and the launcher's steps."""

from pathlib import Path
from typing import Any

import pytest
from ikars_engine import launcher, runmode
from ikars_engine.paths import Paths, resolve


def test_the_installed_program_keeps_its_data_in_the_user_s_folder(tmp_path: Path) -> None:
    program = tmp_path / "IKARS-App" / "current" / "python"  # Velopack's install, no .portable
    program.mkdir(parents=True)
    paths = resolve(program, {"LOCALAPPDATA": str(tmp_path / "local")})
    assert paths.root == tmp_path / "local" / "IKARS"  # apart from the program's IKARS-App


def test_a_velopack_portable_zip_keeps_its_data_beside_current_never_inside(tmp_path: Path) -> None:
    root = tmp_path / "anywhere"
    program = root / "current" / "python"
    program.mkdir(parents=True)
    (root / ".portable").write_text("", encoding="utf-8")
    paths = resolve(program, {"LOCALAPPDATA": str(tmp_path / "local")})
    assert paths.root == root / "IKARS-data"  # an update replaces current\, never this
    # a python folder that is not Velopack's current\python is no portable root
    other = tmp_path / "x" / "python"
    other.mkdir(parents=True)
    (tmp_path / ".portable").write_text("", encoding="utf-8")
    assert resolve(other, {"LOCALAPPDATA": str(tmp_path / "local")}).root.name == "IKARS"


def test_uninstalling_stops_the_engine_removes_the_windows_entries_and_keeps_the_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(tmp_path).create()
    ledger = paths.ledger / "ledger.sqlite"
    ledger.write_bytes(b"the tax ledger")
    paths.settings.write_text("version = 5\n", encoding="utf-8")
    running = [True, True, False]
    monkeypatch.setattr(runmode, "engine_running", lambda folder: running.pop(0))
    removed: list[bool] = []
    launcher.before_uninstall(paths, remove=lambda: removed.append(True), sleep=lambda s: None)
    assert runmode.stop_requested(paths.data) and runmode.stopped_by_owner(paths.data)
    assert removed == [True]
    assert ledger.read_bytes() == b"the tax ledger" and paths.settings.exists()


def test_uninstalling_a_stopped_engine_only_removes_the_windows_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(tmp_path).create()
    monkeypatch.setattr(runmode, "engine_running", lambda folder: False)
    removed: list[bool] = []
    launcher.before_uninstall(paths, remove=lambda: removed.append(True))
    assert removed == [True] and not runmode.stop_requested(paths.data)


def test_a_plain_start_opens_the_terminal_and_a_failure_is_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ikars_engine import open_terminal

    paths = Paths(tmp_path / "IKARS")
    monkeypatch.setattr(launcher, "resolve", lambda: paths)
    opened: list[Any] = []
    monkeypatch.setattr(open_terminal, "open_terminal", lambda p, **kw: opened.append(p))
    assert launcher.main([]) == 0 and opened == [paths]  # no Velopack step: the terminal opens
    shown: list[str] = []
    monkeypatch.setattr(launcher, "say", shown.append)

    def fails(p: Paths, **kw: Any) -> None:
        raise open_terminal.OpenError("Microsoft Edge was not found")

    monkeypatch.setattr(open_terminal, "open_terminal", fails)
    assert launcher.main([]) == 1 and "Edge was not found" in shown[0]
