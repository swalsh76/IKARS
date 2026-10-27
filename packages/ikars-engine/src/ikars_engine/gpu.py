"""GPU detection (PLAN D15; moved to M1.6 by the owner, 2026-10-27).

At start the engine asks ``nvidia-smi`` (installed with NVIDIA's driver; no package) for the card's
name, memory and driver, and reads the CUDA version from its header ("CUDA Version" on older
drivers, "CUDA UMD Version" on newer ones). Without an NVIDIA card or driver, health says "CPU
only": every GPU use in IKARS has a CPU path (D15). Detection runs once, in a thread, with a
timeout.
"""

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

QUERY = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,driver_version",
    "--format=csv,noheader,nounits",
]
CUDA = re.compile(r"CUDA(?: UMD)? Version:\s*([0-9]+(?:\.[0-9]+)*)")


@dataclass(frozen=True, slots=True)
class Gpu:
    name: str
    memory_mb: int
    driver: str
    cuda: str | None


def parse(query: str, header: str) -> list[Gpu]:
    """The cards in ``nvidia-smi``'s CSV answer, each with the CUDA version from its header."""
    found = CUDA.search(header)
    cuda = found.group(1) if found else None
    cards = []
    for line in query.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3 and parts[1].isdigit():
            cards.append(Gpu(parts[0], int(parts[1]), parts[2], cuda))
    return cards


def _run(argv: list[str]) -> str:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    done = subprocess.run(  # noqa: S603 - fixed arguments (nvidia-smi), no shell
        argv, capture_output=True, text=True, timeout=10, creationflags=flags, check=True
    )
    return done.stdout


def detect(run: Callable[[list[str]], str] = _run) -> list[Gpu]:
    """The NVIDIA cards present, or [] (no card, no driver, or ``nvidia-smi`` failed)."""
    try:
        return parse(run(QUERY), run(["nvidia-smi"]))
    except (OSError, subprocess.SubprocessError):
        return []


def health(cards: list[Gpu]) -> dict[str, object]:
    if not cards:
        return {"device": None, "note": "CPU only"}
    first = cards[0]
    return {"device": first.name, "memory_mb": first.memory_mb, "driver": first.driver,
            "cuda": first.cuda, "count": len(cards)}  # fmt: skip
