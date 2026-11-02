"""Small files written whole (a crash or a power cut leaves the old version or the new, never half).

Used for the engine's health snapshot, the watchdog's and the alert desk's memories, and anything
else small that another process reads while it may be rewritten.
"""

import os
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

REPLACE_TRIES = 5  # a reader in another process holds the file briefly (Windows refuses the rename)
REPLACE_PAUSE_S = 0.02


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` to a temporary file, flush it to disk, then rename it over ``path``. The
    temporary name is unique, so two processes writing one file never share it (M1.R3 A3)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex[:12]}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(REPLACE_TRIES):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == REPLACE_TRIES - 1:
                    raise
                time.sleep(REPLACE_PAUSE_S)
    finally:
        temporary.unlink(missing_ok=True)


def first_free(candidates: Iterable[Path]) -> Path:
    """The first of ``candidates`` that does not exist (an endless iterable)."""
    return next(path for path in candidates if not path.exists())


def backup_name(path: Path) -> Path:
    """A name beside ``path`` for a copy of it that never overwrites an earlier copy."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return first_free(
        path.with_name(f"{path.stem}.{stamp}{'' if n == 0 else f'-{n}'}.bak{path.suffix}")
        for n in count()
    )
