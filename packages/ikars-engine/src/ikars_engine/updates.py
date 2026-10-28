"""Updates (M1.9b; owner, 2026-10-27: notified, installed only on the owner's click).

The installed program checks GitHub Releases at start (after a minute) and then daily, and
downloads a newer version in the background; Health and Settings show it. Nothing is installed
until the owner clicks Restart to update: then Velopack's ``Update.exe`` waits for this engine to
stop, swaps the program folder (the data folder is never touched), and starts ``IKARS.exe``,
which opens the terminal and starts the new engine; its migrations back up first, as always.

A development checkout (no ``Update.exe`` above the engine's Python) has no updates.
"""

import asyncio
import contextlib
import logging
import re
import sys
import threading
from collections import deque
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
EVENTS_KEPT = 20
# IKARS.exe after an update: the engine only, no new window (the one that asked reconnects)
AFTER_UPDATE = "--after-update"

NOT_INSTALLED = "not installed"  # a development checkout
UP_TO_DATE = "up to date"
CHECKING = "checking"
DOWNLOADING = "downloading"
READY = "ready"  # downloaded: Restart to update installs it
INSTALLING = "installing"  # handed to Velopack: the engine stops for it
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


def program_version() -> str | None:
    """The installed program's version (Velopack's manifest), or None in a development checkout."""
    where = locator()
    if where is None:
        return None
    try:  # Velopack's manifest (a nuspec): no update manager for a version (merge review 9)
        text = Path(where.ManifestPath).read_text(encoding="utf-8")
    except OSError:  # a broken install: no version, so no snapshot is keyed to a wrong one
        return None
    found = re.search(r"<version>\s*([^<\s]+)\s*</version>", text)
    return found.group(1) if found else None


def downgrade_manager(version: str, where: Any) -> Any:
    """A manager for one earlier release (its own GitHub release's feed), allowed to go back."""
    from velopack import HttpSource, UpdateManager, UpdateOptions

    options = UpdateOptions(AllowVersionDowngrade=True, MaximumDeltasBeforeFallback=10)
    source = HttpSource(f"{REPOSITORY}/releases/download/v{version}")
    return UpdateManager(source, options, where)


@dataclass
class Updater:
    """One engine's updates. ``manager`` is Velopack's (None: not installed)."""

    manager: Any = None
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    state: str = NOT_INSTALLED
    available: str | None = None
    checked_at: datetime | None = None
    problem: str | None = None
    progress: int | None = None
    size_bytes: int | None = None
    events: deque[str] = field(default_factory=lambda: deque(maxlen=EVENTS_KEPT))
    version: str | None = None  # read once: Velopack's manager allows one thread at a time
    _pending: Any = None
    # every use of the manager holds this: a second thread raises "Already mutably borrowed"
    # inside Velopack (the owner's 0.1.4 update: the version box failed while a check ran)
    _turn: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if self.manager is not None and self.version is None:
            self.version = str(self.manager.get_current_version())

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
        return self.version

    def view(self) -> c.UpdateView:
        at = None if self.checked_at is None else self.checked_at.isoformat(timespec="seconds")
        return c.UpdateView(installed=self.manager is not None, current=self.current(),
                            available=self.available, state=self.state, checked_at_utc=at,
                            problem=self.problem, progress=self.progress,
                            size_bytes=self.size_bytes, events=list(self.events))  # fmt: skip

    def _event(self, text: str) -> None:
        self.events.append(f"{self.now():%H:%M:%S} {text}")

    def _downloaded(self, percent: Any) -> None:
        """Velopack's progress, from its download thread (a number from 0 to 100)."""
        with contextlib.suppress(TypeError, ValueError):  # never a crash from Velopack's thread
            self.progress = max(0, min(100, int(percent)))

    def check(self) -> c.UpdateView:
        """Look for a newer release and download it (blocking: run it in a thread)."""
        if self.manager is None or not self._turn.acquire(blocking=False):
            return self.view()  # nothing to check, or a check already running
        if self.state == INSTALLING:  # handed to Velopack already
            self._turn.release()
            return self.view()
        ready = self._pending if self.state == READY else None  # kept if this check fails
        try:
            self.state, self.problem = CHECKING, None
            self._event("checking GitHub for a newer version")
            found = self.manager.check_for_updates()
            if found is None:
                self.state, self.available, self._pending = UP_TO_DATE, None, None
                self._event("up to date")
            else:
                self.available = str(found.TargetFullRelease.Version)
                deltas = list(found.DeltasToTarget or [])  # a delta when there is one: smaller
                assets = deltas or [found.TargetFullRelease]
                self.size_bytes = sum(int(a.Size) for a in assets)
                self.state, self.progress = DOWNLOADING, 0
                self._event(f"downloading {self.available} ({self.size_bytes / 1e6:.1f} MB)")
                self.manager.download_updates(found, self._downloaded)
                self._pending, self.progress, self.state = found, 100, READY
                self._event(f"downloaded {self.available}")
        except Exception as exc:  # offline, rate-limited, a bad package: said, retried tomorrow
            self.problem = f"{type(exc).__name__}: {exc}"[:300]
            self._event(f"failed: {self.problem}")
            # a release already downloaded stays ready to install (merge review 4)
            self.state, self._pending = (READY, ready) if ready is not None else (FAILED, None)
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
        if not self._turn.acquire(blocking=False):
            return c.Done(done=False, detail="a check is running: try again in a moment")
        try:
            self.manager.wait_exit_then_apply_updates(
                self._pending, silent=True, restart=True, restart_args=[AFTER_UPDATE]
            )
            self.state = INSTALLING  # a second click finds nothing to hand over (merge review 7)
        finally:
            self._turn.release()
        self._event(f"installing {self.available}: the engine stops for it")
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
            if self.state in (READY, INSTALLING):  # waiting for the owner: nothing new to fetch
                await asyncio.sleep(CHECK_EVERY_S)
                continue
            try:
                await asyncio.wait_for(in_daemon_thread(self.check), CHECK_WAIT_S)
            except TimeoutError:
                self.state, self.problem = FAILED, "the check did not finish in time"
            await asyncio.sleep(CHECK_EVERY_S)
