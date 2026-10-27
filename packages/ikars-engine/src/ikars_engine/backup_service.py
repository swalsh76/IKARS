"""Backups while the engine runs (PLAN D18): once a day, and at start when the last one is older.

The copying runs in a daemon thread (it opens its own SQLite connections), so the feed is never
held up, and a NAS that stops answering never holds up the engine's exit. The time of the last
good backup is a bookmark in the state store; a failed or skipped backup is counted and shown in
health (by type: a message could name the share). M1.6 adds the logon task, so backups run
whenever the engine does.
"""

import asyncio
import logging

from ikars_engine.clocks import EventClock
from ikars_engine.ledger import iso_utc
from ikars_engine.paths import Paths
from ikars_engine.share import BackupReport, Share, back_up
from ikars_engine.store import StateStore
from ikars_engine.threads import in_daemon_thread

log = logging.getLogger("ikars_engine.backup")
LAST_OK = "backup.last_ok_ns"
NS = 1_000_000_000


class BackupService:
    def __init__(
        self,
        share: Share | None,
        paths: Paths,
        state: StateStore,
        clock: EventClock,
        *,
        check_every_s: float = 3_600.0,
        due_after_s: float = 86_400.0,
    ) -> None:
        self._share = share
        self._paths = paths
        self._state = state
        self._clock = clock
        self._every = check_every_s
        self._due_after_ns = int(due_after_s * NS)
        self._task: asyncio.Task[None] | None = None
        self.runs = 0
        self.failures = 0
        self.last_error: str | None = None
        self.last: BackupReport | None = None

    async def start(self) -> None:
        if self._share is not None:
            self._task = asyncio.create_task(self._loop(), name="backup")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await self.run_if_due()
            await asyncio.sleep(self._every)

    async def run_if_due(self) -> bool:
        last = self._state.bookmark(LAST_OK)
        if last is not None and self._clock.now_ns() - last < self._due_after_ns:
            return False
        await self.run_now()
        return True

    async def run_now(self) -> None:
        if self._share is None:
            return
        now = self._clock.now_ns()
        self.runs += 1
        try:
            self.last = await in_daemon_thread(
                back_up,
                self._share,
                ledger=self._paths.ledger / "ledger.sqlite",
                state=self._paths.data / "state.sqlite",
                settings=self._paths.settings,
                checkpoints=self._paths.ledger / "checkpoints",
                staging=self._paths.data / "backup-staging",
                now_ns=now,
            )
        except Exception as exc:  # shown in health; the next hour tries again
            self.failures += 1
            self.last_error = type(exc).__name__
            log.warning("backup failed", extra={"error": self.last_error})
            return
        self.last_error = None
        self._state.set_bookmark(LAST_OK, now)
        log.info("backup done", extra={"pruned": self.last.pruned})

    def health(self) -> dict[str, object]:
        last = self._state.bookmark(LAST_OK)
        return {
            # nothing here touches the share: an unreachable NAS must never stall the event loop
            "configured": self._share is not None,
            "last_ok_utc": None if last is None else iso_utc(last),
            "hours_since": None if last is None else (self._clock.now_ns() - last) // (3_600 * NS),
            "runs": self.runs,
            "failures": self.failures,
            "last_error": self.last_error,
        }
