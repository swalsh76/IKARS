"""The ledger inside the running engine (PLAN D18): verified at start, the account imported every
minute, a checkpoint after each import, and IKARS's own actions recorded in the same chain.

A chain that fails verification stops all appends (nothing is built on a broken record) and health
says so; the feed keeps running. Health shows counts and status only, never amounts.
"""

import asyncio
import logging
from dataclasses import replace
from pathlib import Path

from ikars_engine.clocks import EventClock
from ikars_engine.ledger import Entry, Ledger, LedgerError, Verified
from ikars_engine.ledger_import import AccountImporter, ImportReport
from ikars_engine.taxzone import TaxZone

log = logging.getLogger("ikars_engine.ledger")
SETTINGS_HASH = "app.settings_sha256"
ANNOTATION_KINDS = frozenset({"interest", "incentive", "fee", "funding", "adjustment"})


def app_action(ledger: Ledger, now_ns: int, action: str, **fields: object) -> None:
    ledger.append(Entry("app", now_ns, "app", fields={"action": action, **fields}))


def annotate(
    ledger: Ledger,
    kind: str,
    amount_e6: int,
    at_ns: int,
    note: str,
    corrects: int | None = None,
) -> int:
    """The owner's label for money the import cannot explain (interest, an incentive), or a
    correction of an earlier record: always a new record, never an edit."""
    if kind not in ANNOTATION_KINDS:
        raise LedgerError(f"an annotation is one of {sorted(ANNOTATION_KINDS)}, not {kind!r}")
    if not note.strip():
        raise LedgerError("an annotation needs a note saying what it is")
    if corrects is not None and not 1 <= corrects <= ledger.head().seq:
        raise LedgerError(f"there is no record {corrects} to correct")
    # an annotation has no source id, so it is never skipped as already in
    (seq,) = ledger.append_all(
        [Entry(kind, at_ns, "annotation", cash_e6=amount_e6, note=note.strip(), corrects=corrects)]
    )
    return seq


class LedgerService:
    def __init__(
        self,
        ledger: Ledger,
        importer: AccountImporter,
        checkpoints: Path,
        clock: EventClock,
        zone: TaxZone,
        *,
        key_fingerprint: str | None = None,
        settings_sha256: str | None = None,
        every_s: float = 60.0,
    ) -> None:
        self._ledger = ledger
        self._importer = importer
        self._checkpoints = checkpoints
        self._clock = clock
        self._zone = zone
        self._key = key_fingerprint
        self._settings = settings_sha256
        self._every = every_s
        self._task: asyncio.Task[None] | None = None
        self.verified: Verified | None = None
        self.last: ImportReport | None = None
        self.imports = 0
        self.errors = 0

    @property
    def writable(self) -> bool:
        return self.verified is not None and self.verified.ok

    def check(self) -> Verified:
        """Walk the chain and compare it with the newest checkpoint. The runner calls this before
        the feed starts, so a long walk never delays a frame."""
        verified = self._ledger.verify()
        problem = self._ledger.against_checkpoint(self._checkpoints) if verified.ok else None
        self.verified = (
            verified if problem is None else replace(verified, ok=False, problem=problem)
        )
        return self.verified

    async def start(self) -> None:
        verified = self.verified if self.verified is not None else self.check()
        if not verified.ok:
            log.error(
                "ledger chain broken; nothing will be appended", extra={"problem": verified.problem}
            )
            return
        now = self._clock.now_ns()
        zone = {"tax_zone": self._zone.name, "tax_zone_source": self._zone.source}
        app_action(self._ledger, now, "engine_started", **zone)
        if self._key is not None:
            app_action(self._ledger, now, "key_loaded", fingerprint=self._key)
        if self._settings is not None and self._ledger.bookmark(SETTINGS_HASH) != self._settings:
            app_action(self._ledger, now, "settings_changed", sha256=self._settings)
            self._ledger.set_bookmark(SETTINGS_HASH, self._settings)
        self._task = asyncio.create_task(self._loop(), name="ledger.import")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self.writable:
            app_action(self._ledger, self._clock.now_ns(), "engine_stopped")
            self._ledger.checkpoint(self._checkpoints, self._clock.now_ns())

    async def _loop(self) -> None:
        while True:
            await self.import_once()
            await asyncio.sleep(self._every)

    async def import_once(self) -> None:
        try:
            self.last = await self._importer.run_once()
            self.imports += 1
            self._ledger.checkpoint(self._checkpoints, self._clock.now_ns())
        except Exception as exc:  # the next minute tries again
            self.errors += 1
            # the type only: a message could quote an account value
            log.warning("ledger import failed", extra={"error": type(exc).__name__})

    def health(self) -> dict[str, object]:
        reconciliation = None if self.last is None else self.last.reconciliation
        return {
            "verified": None if self.verified is None else self.verified.ok,
            "problem": None if self.verified is None else self.verified.problem,
            "records": self._ledger.count(),
            "imports": self.imports,
            "errors": self.errors,
            "added_last": None if self.last is None else sum(self.last.added.values()),
            "reconciled_exact": None if reconciliation is None else reconciliation.exact,
            "cash_matches": None if reconciliation is None else reconciliation.difference_e6 == 0,
            "position_differences": None
            if reconciliation is None
            else len(reconciliation.positions),
            "tax_zone": self._zone.name,
            "tax_zone_source": self._zone.source,
        }
