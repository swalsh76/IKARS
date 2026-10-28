"""M1.9b: updates, notified and installed only on the owner's click (Velopack is a fake here)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from ikars_engine import runmode, updates
from ikars_engine.autostart import MemoryRegistry
from ikars_engine.paths import Paths
from ikars_engine.settings import Settings
from ikars_engine.settings_api import TerminalConfig
from ikars_engine.updates import READY, UP_TO_DATE, Updater


@dataclass
class Release:
    Version: str


@dataclass
class Found:
    TargetFullRelease: Release


@dataclass
class FakeManager:
    """Velopack's UpdateManager, as far as the updater uses it."""

    found: Found | None = None
    fails: Exception | None = None
    applied: list[tuple[Any, bool, bool, list[str]]] = field(default_factory=list)
    downloaded: list[Any] = field(default_factory=list)

    def get_current_version(self) -> str:
        return "0.1.0"

    def check_for_updates(self) -> Found | None:
        if self.fails is not None:
            raise self.fails
        return self.found

    def download_updates(self, found: Found) -> None:
        self.downloaded.append(found)

    def wait_exit_then_apply_updates(
        self, found: Found, silent: bool, restart: bool, restart_args: list[str]
    ) -> None:
        self.applied.append((found, silent, restart, restart_args))


AT = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def test_a_development_checkout_has_no_updates(tmp_path: Path) -> None:
    assert updates.locator(tmp_path / ".venv" / "Scripts" / "python.exe") is None
    current = tmp_path / "IKARS-App" / "current" / "python"
    current.mkdir(parents=True)
    assert updates.locator(current / "pythonw.exe") is None  # no Update.exe: not installed
    view = Updater().view()
    assert view.installed is False and view.state == "not installed" and view.current is None


def test_the_installed_program_s_folders_are_found_from_the_engine_s_python(tmp_path: Path) -> None:
    root = tmp_path / "IKARS-App"
    (root / "current" / "python").mkdir(parents=True)
    (root / "Update.exe").write_bytes(b"")
    where = updates.locator(root / "current" / "python" / "pythonw.exe")
    assert Path(where.RootAppDir) == root.resolve()
    assert Path(where.UpdateExePath) == (root / "Update.exe").resolve()
    assert Path(where.CurrentBinaryDir) == (root / "current").resolve()
    assert Path(where.ManifestPath) == (root / "current" / "sq.version").resolve()
    assert where.IsPortable is False


def test_a_newer_release_is_downloaded_and_waits_for_the_owner(tmp_path: Path) -> None:
    manager = FakeManager(found=Found(Release("0.1.1")))
    updater = Updater(manager=manager, state=UP_TO_DATE, now=lambda: AT)
    view = updater.check()
    assert view.state == READY and view.available == "0.1.1" and view.current == "0.1.0"
    assert manager.downloaded and not manager.applied  # nothing installed until the click
    assert view.checked_at_utc == "2026-10-27T12:00:00+00:00"
    stopped: list[bool] = []
    done = updater.apply(lambda: stopped.append(True))
    assert done.done and "0.1.1" in done.detail
    # restarted with the engine only: the window that asked reconnects, no second one opens
    assert manager.applied == [(manager.found, True, True, ["--after-update"])]
    assert stopped == [True]


def test_no_release_or_a_failed_check_is_said(tmp_path: Path) -> None:
    updater = Updater(manager=FakeManager(), state=UP_TO_DATE, now=lambda: AT)
    assert updater.check().state == UP_TO_DATE
    assert updater.apply(lambda: None).done is False  # nothing ready
    failing = Updater(manager=FakeManager(fails=OSError("offline")), state=UP_TO_DATE)
    view = failing.check()
    assert view.state == "failed" and "offline" in (view.problem or "")


async def test_a_program_without_updates_never_checks() -> None:
    await Updater().keep_checking()  # returns at once: nothing to check


async def test_the_update_routes(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from ikars_engine.api import ApiContext, create_app
    from ikars_engine.kalshi.discovery import MarketIndex
    from ikars_engine.ledger import Ledger
    from ikars_engine.metrics import Metrics
    from ikars_engine.portfolio import Held, portfolio

    paths = Paths(tmp_path / "IKARS").create()
    ledger = Ledger(tmp_path / "ledger.sqlite", ZoneInfo("America/Denver"))
    manager = FakeManager(found=Found(Release("0.1.1")))

    def context(restartable: bool, updater: Updater | None) -> ApiContext:
        return ApiContext(
            token="t" * 43, health=dict, index=MarketIndex(), quotes={}, last_price={},
            changed_ns={}, book_changed_ns={}, books={}, book_version={}, watched=set,
            ledger=ledger, ledger_health=dict,
            portfolio=lambda: portfolio({}, lambda s: None, lambda s: None, Held({}, 0)),
            alert_log=tmp_path / "alerts.jsonl", metrics=Metrics(), now_ns=lambda: 0,
            port=47821, updates=updater,
            config=TerminalConfig(paths, Settings(), None, MemoryRegistry(), lambda a: 0,
                                  restartable=restartable),
        )  # fmt: skip

    auth = {"Authorization": f"Bearer {'t' * 43}"}
    base = "http://127.0.0.1"
    app = create_app(context(False, None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as http:
        assert (await http.get("/v1/update", headers=auth)).json()["installed"] is False
    updater = Updater(manager=manager, state=UP_TO_DATE)
    app = create_app(context(False, updater))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as http:
        checked = (await http.post("/v1/update/check", headers=auth)).json()
        assert checked["state"] == READY and checked["available"] == "0.1.1"
        refused = (await http.post("/v1/update/apply", headers=auth)).json()
        assert refused["done"] is False and "foreground" in refused["detail"]
    app = create_app(context(True, updater))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base) as http:
        assert (await http.post("/v1/update/apply", headers=auth)).json()["done"] is True
    assert manager.applied and runmode.stop_requested(paths.data)  # the engine stops for it
    ledger.close()


def test_for_this_program_in_development_has_no_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates, "locator", lambda executable=None: None)
    assert Updater.for_this_program().manager is None
