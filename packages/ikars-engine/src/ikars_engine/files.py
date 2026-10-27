"""Small files written whole (a crash or a power cut leaves the old version or the new, never half).

Used for the engine's health snapshot, the watchdog's and the alert desk's memories, and anything
else small that another process reads while it may be rewritten.
"""

import os
from datetime import UTC, datetime
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


def backup_name(path: Path) -> Path:
    """A name beside ``path`` for a copy of it that never overwrites an earlier copy."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.stem}.{stamp}.bak{path.suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.stem}.{stamp}-{n}.bak{path.suffix}")
        n += 1
    return target
