"""The engine in the background (PLAN D19 G5): one instance, a clean stop, a health snapshot, awake.

- **One instance.** ``serve`` holds a lock on ``engine.lock`` in the data folder for as long as it
  runs. Windows releases the lock when the process ends, however it ends, so a crash never leaves
  a stale lock; another ``serve`` (the auto-start, the watchdog) finds it held and stops at once.
- **Stopping.** ``stop`` writes ``stop.request``; the engine sees it within seconds and shuts down
  cleanly. ``stop`` also writes ``stopped-by-owner``, so the watchdog does not restart an engine the
  owner stopped; the next ``serve`` removes both.
- **The health snapshot.** Every minute the engine writes ``health.json`` (counts, ages and
  booleans only): what the watchdog and ``status`` read. M1.7's API replaces it.
- **Awake.** While it runs, the engine asks Windows to keep the **system** awake (the display may
  still turn off: owner, 2026-10-27); Windows drops the request when the engine stops.
"""

import contextlib
import ctypes
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from ikars_engine.files import write_text_atomic

LOCK = "engine.lock"
STOP = "stop.request"
STOPPED_BY_OWNER = "stopped-by-owner"
RESTART = "restart.request"  # with stop.request: a fresh engine once this one has stopped
SNAPSHOT = "health.json"
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class AlreadyRunning(RuntimeError):
    pass


class EngineLock:
    """The single-instance lock: held while this process runs the engine."""

    def __init__(self, folder: Path) -> None:
        self.path = folder / LOCK
        self._handle: IO[str] | None = None

    def acquire(self, wait_s: float = 0.0) -> None:
        """Take the lock, trying for ``wait_s`` seconds: a ``status`` or a watchdog probing it
        holds it for an instant, and must not turn a starting engine away."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 (held for the run)
        deadline = time.monotonic() + wait_s
        while True:
            try:
                _lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise AlreadyRunning("the engine is already running") from None
                time.sleep(0.2)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is not None:
            with contextlib.suppress(OSError):
                _unlock(self._handle)
            self._handle.close()
            self._handle = None


def engine_running(folder: Path) -> bool:
    """True while some process holds the engine's lock. The probe takes the lock for an instant
    and writes nothing (an engine starting meanwhile waits: see :meth:`EngineLock.acquire`)."""
    path = folder / LOCK
    if not path.exists():
        return False
    with open(path, "a+", encoding="utf-8") as handle:
        try:
            _lock(handle)
        except OSError:
            return True
        _unlock(handle)
    return False


def _lock(handle: IO[str]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:  # pragma: no cover - IKARS targets Windows; kept so the tests run anywhere
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: IO[str]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


# --- stopping -----------------------------------------------------------------------------------


def request_stop(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STOP).write_text("stop\n", encoding="utf-8")
    (folder / STOPPED_BY_OWNER).write_text("the owner stopped the engine\n", encoding="utf-8")


def stop_requested(folder: Path) -> bool:
    return (folder / STOP).exists()


def stopped_by_owner(folder: Path) -> bool:
    return (folder / STOPPED_BY_OWNER).exists()


def clear_stop(folder: Path) -> None:
    for name in (STOP, STOPPED_BY_OWNER, RESTART):  # a start spends a stale restart request too
        (folder / name).unlink(missing_ok=True)


# --- the health snapshot ------------------------------------------------------------------------


def write_snapshot(folder: Path, snapshot: dict[str, Any]) -> None:
    write_text_atomic(folder / SNAPSHOT, json.dumps(snapshot, default=str))


def starting_snapshot(now_ns: int, waiting_s: int | None = None) -> dict[str, Any]:
    """Written as soon as ``serve`` holds the lock, so the watchdog never judges a new engine by
    the last run's snapshot; it allows a start-up grace (discovery takes a minute or two). Written
    again while the engine waits for the network, with how long it has waited."""
    snapshot: dict[str, Any] = {"written_ns": now_ns, "pid": os.getpid(), "starting": True}
    if waiting_s is not None:
        snapshot["waiting_for_network_s"] = waiting_s
    return snapshot


def read_snapshot(folder: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((folder / SNAPSHOT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def at(tree: Any, *keys: str) -> Any:
    """``tree[k1][k2]...``, or None where a level is missing or not a mapping."""
    for key in keys:
        if not isinstance(tree, dict):
            return None
        tree = tree.get(key)
    return tree


def snapshot_of(health: dict[str, Any], now_ns: int) -> dict[str, Any]:
    """The part of the engine's health the watchdog needs: ages, counts and booleans only."""
    return {
        "written_ns": now_ns,
        "pid": os.getpid(),
        "starting": False,
        "feed": {
            "connected": at(health, "kalshi", "feed", "connected"),
            "seconds_since_last_frame": at(health, "kalshi", "feed", "seconds_since_last_frame"),
        },
        "trading_active": at(health, "kalshi", "trading_active"),
        "open_gaps": at(health, "kalshi", "open_gaps"),
        "storage_state": at(health, "kalshi", "storage", "state"),
        "ledger": {
            "verified": at(health, "ledger", "verified"),
            "reconciled_exact": at(health, "ledger", "reconciled_exact"),
            "errors": at(health, "ledger", "errors"),
        },
        "backup": {
            "configured": at(health, "backup", "configured"),
            "hours_since": at(health, "backup", "hours_since"),
            "last_error": at(health, "backup", "last_error"),
        },
        "gpu": at(health, "gpu", "device"),
        "port_fallback": at(health, "api", "port_fallback"),
        "setup": at(health, "setup", "reason"),  # setup mode: why the key cannot be used
    }


# --- awake --------------------------------------------------------------------------------------


@contextlib.contextmanager
def keep_awake() -> Iterator[bool]:
    """Keep the system (not the display) awake for as long as the block runs; yields whether
    Windows took the request."""
    if sys.platform != "win32":  # pragma: no cover
        yield False
        return
    kernel = ctypes.WinDLL("kernel32")
    kernel.SetThreadExecutionState.restype = ctypes.c_uint32
    kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    taken = bool(kernel.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
    try:
        yield taken
    finally:
        kernel.SetThreadExecutionState(ES_CONTINUOUS)
