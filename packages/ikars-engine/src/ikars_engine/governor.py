"""The storage governor: the raw recordings stay inside their budget (PLAN D19 G11; owner: 200 GB).

Only compacted recordings (``<channel>/<YYYY-MM-DD>/<HH>.parquet``) are ever pruned, oldest hour
first across all channels. An open hour (``.arrow``), the ledger and the state store are never
touched. At the warning level the health view says so; at the pruning level the oldest hours are
deleted until usage is back below the target, and every deletion is reported.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

GB = 1_000_000_000


class Storage(StrEnum):
    OK = "ok"
    WARN = "warn"
    PRUNED = "pruned"


@dataclass(frozen=True, slots=True)
class StorageReport:
    state: Storage
    used_bytes: int
    budget_bytes: int
    pruned: list[Path] = field(default_factory=list)

    @property
    def used_percent(self) -> float:
        return 100.0 * self.used_bytes / self.budget_bytes if self.budget_bytes else 0.0


class StorageGovernor:
    def __init__(
        self,
        root: Path,
        budget_bytes: int = 200 * GB,
        *,
        warn_percent: int = 80,
        prune_percent: int = 95,
        target_percent: int = 90,
    ) -> None:
        if not 0 < target_percent < prune_percent <= 100 or not 0 < warn_percent <= prune_percent:
            raise ValueError("need 0 < target < prune <= 100 and 0 < warn <= prune")
        self.root = root
        self.budget_bytes = budget_bytes
        self._warn = warn_percent
        self._prune = prune_percent
        self._target = target_percent

    def _files(self) -> list[Path]:
        return [
            p for p in self.root.glob("*/*/*") if p.suffix in (".parquet", ".arrow", ".damaged")
        ]

    def _sizes(self) -> dict[Path, int]:
        """Each file's size; one renamed or removed since the listing (a compaction running in a
        worker thread) is left out (M1.R 20)."""
        sizes: dict[Path, int] = {}
        for path in self._files():
            try:
                sizes[path] = path.stat().st_size
            except FileNotFoundError:
                continue
        return sizes

    def used_bytes(self) -> int:
        return sum(self._sizes().values())

    def check(self) -> StorageReport:
        sizes = self._sizes()
        files = list(sizes)
        used = sum(sizes.values())
        if used * 100 < self.budget_bytes * self._warn:
            return StorageReport(Storage.OK, used, self.budget_bytes)
        if used * 100 < self.budget_bytes * self._prune:
            return StorageReport(Storage.WARN, used, self.budget_bytes)
        # oldest hour first across channels: by (date, hour), then channel for a stable order
        compacted = sorted(
            (p for p in files if p.suffix in (".parquet", ".damaged")),  # never an open hour
            key=lambda p: (p.parent.name, p.stem, p.parent.parent.name),
        )
        pruned: list[Path] = []
        for path in compacted:
            if used * 100 < self.budget_bytes * self._target:
                break
            try:
                path.unlink()
            except FileNotFoundError:
                continue  # renamed or removed meanwhile: counted where it is now, next time
            except PermissionError:
                continue  # held by a reader: pruned next time
            used -= sizes[path]
            pruned.append(path)
        return StorageReport(Storage.PRUNED, used, self.budget_bytes, pruned)
