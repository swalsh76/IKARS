"""Updates (M1.9b; owner, 2026-10-27: notified, installed only on the owner's click).

The installed program checks GitHub Releases at start (after a minute) and then daily, and
downloads a newer version in the background; Health and Settings show it. Nothing is installed
until the owner clicks Restart to update: then Velopack's ``Update.exe`` waits for this engine to
stop, swaps the program folder (the data folder is never touched), and starts ``IKARS.exe``,
which opens the terminal and starts the new engine; its migrations back up first, as always.

A development checkout (no ``Update.exe`` above the engine's Python) has no updates.
"""

import asyncio
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ikars_engine import contracts as c
from ikars_engine.threads import in_daemon_thread

log = logging.getLogger("ikars_engine.updates")
REPOSITORY = "https://github.com/swalsh76/IKARS"
FIRST_CHECK_S = 60.0  # after the start: never in the way of a cold start
CHECK_EVERY_S = 24 * 3600.0
CHECK_WAIT_S = 600.0  # a check and its download (a full package is about 65 MB)

NOT_INSTALLED = "not installed"  # a development checkout
UP_TO_DATE = "up to date"
CHECKING = "checking"
READY = "ready"  # downloaded: Restart to update installs it
FAILED = "failed"


def locator(executable: Path | None = None) -> Any:
    """Velopack's paths for the installed program, or None in a development checkout. The engine
    runs ``<root>\\current\\python\\pythonw.exe``, not the main exe Velopack looks for itself."""
    from velopack import VelopackLocatorConfig

    python = (executable or Path(sys.executable)).resolve().parent
    current = python.parent
    root = current.parent
    update = root / "Update.exe"
    if python.name.lower() != "python" or current.name.lower() != "current":
        return None
    if not update.is_file():
        return None
    return VelopackLocatorConfig(
        RootAppDir=root,
        UpdateExePath=update,
        PackagesDir=root / "packages",
        ManifestPath=current / "sq.version",
        CurrentBinaryDir=current,
        IsPortable=(root / ".portable").is_file(),
    )


def github_manager(where: Any) -> Any:
    from velopack import GithubSource, UpdateManager

    # pre-releases too: M1.9's releases are pre-releases until signing is decided (M6)
    return UpdateManager(GithubSource(REPOSITORY, prerelease=True), locator=where)


@dataclass
class Updater:
    """One engine's updates. ``manager`` is Velopack's (None: not installed)."""

    manager: Any = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    state: str = NOT_INSTALLED
    available: str | None = None
    checked_at: datetime | None = None
    problem: str | None = None
    _pending: Any = None
    _turn: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def for_this_program(cls) -> "Updater":
        where = locator()
        if where is None:
            return cls()
        try:
            return cls(manager=github_manager(where), state=UP_TO_DATE)
        except Exception as exc:  # a broken install: said, never a crash at start
            log.warning("updates unavailable", extra={"error": f"{type(exc).__name__}: {exc}"})
            return cls(problem=str(exc))

    def current(self) -> str | None:
        return None if self.manager is None else str(self.manager.get_current_version())

    def view(self) -> c.UpdateView:
        at = None if self.checked_at is None else self.checked_at.isoformat(timespec="seconds")
        return c.UpdateView(installed=self.manager is not None, current=self.current(),
                            available=self.available, state=self.state, checked_at_utc=at,
                            problem=self.problem)  # fmt: skip

    def check(self) -> c.UpdateView:
        """Look for a newer release and download it (blocking: run it in a thread)."""
        if self.manager is None or not self._turn.acquire(blocking=False):
            return self.view()  # nothing to check, or a check already running
        try:
            self.state, self.problem = CHECKING, None
            found = self.manager.check_for_updates()
            if found is None:
                self.state, self.available, self._pending = UP_TO_DATE, None, None
            else:
                self.manager.download_updates(found)
                self._pending = found
                self.available = str(found.TargetFullRelease.Version)
                self.state = READY
        except Exception as exc:  # offline, rate-limited, a bad package: said, retried tomorrow
            self.state, self.problem = FAILED, f"{type(exc).__name__}: {exc}"[:300]
            log.warning("update check failed", extra={"error": self.problem})
        finally:
            self.checked_at = self.now()
            self._turn.release()
        return self.view()

    def apply(self, stop: Callable[[], None]) -> c.Done:
        """Hand the downloaded release to Velopack, which installs it once this engine has stopped
        (``stop``) and then starts the program again."""
        if self.manager is None or self.state != READY or self._pending is None:
            return c.Done(done=False, detail="no update is ready to install")
        self.manager.wait_exit_then_apply_updates(self._pending, silent=True, restart=True)
        stop()
        return c.Done(
            done=True,
            detail=f"installing {self.available}: the engine stops, IKARS restarts with it",
        )

    async def keep_checking(self) -> None:
        """At start (after FIRST_CHECK_S) and then daily, for as long as the engine runs."""
        if self.manager is None:
            return
        await asyncio.sleep(FIRST_CHECK_S)
        while True:
            try:
                await asyncio.wait_for(in_daemon_thread(self.check), CHECK_WAIT_S)
            except TimeoutError:
                self.state, self.problem = FAILED, "the check did not finish in time"
            await asyncio.sleep(CHECK_EVERY_S)
