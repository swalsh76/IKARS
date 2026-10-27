"""Small files written whole (a crash or a power cut leaves the old version or the new, never half).

Used for the engine's health snapshot, the watchdog's and the alert desk's memories, and anything
else small that another process reads while it may be rewritten.
"""

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from itertools import count
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Write ``text`` to a temporary file, flush it to disk, then rename it over ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


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
