"""The settings the terminal shows and changes (M1.8b-1: Settings and the first-run wizard).

The engine's settings file stays the one place they live (versioned, migrated, backed up before
every change). This module reads it for the terminal, checks and saves a change, and does the few
actions the screens offer: test or set up the share, install the start at sign-in, restart.

What applies when:

- the skin, quiet hours, alert throttle and digest apply at once (the terminal and the watchdog read
  them fresh);
- the rest (the tax zone, the share, the feed, the port, the session length) apply after an engine
  restart: ``restart_needed`` names those saved but not in use yet.

The key's status is yes or no only: never a key, its id or its path (PLAN D8). Share work runs in a
thread with a time limit: a slow network share must never stall the engine's event loop.
"""

import asyncio
import os
import secrets
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

import msgspec
from ikars_core.winacl import other_readers

from ikars_engine import autostart, runmode, taxzone
from ikars_engine import contracts as c
from ikars_engine.alerts import QuietHours
from ikars_engine.paths import Paths
from ikars_engine.settings import Settings, SettingsError, Skin, backup, load, save
from ikars_engine.share import MARKER, Share, ShareError
from ikars_engine.threads import in_daemon_thread

SKINS: tuple[str, ...] = get_args(Skin)
SHARE_WAIT_S = 10.0
RESTART = runmode.RESTART
BACKUPS_KEPT = 20  # the settings file's backups kept (each change but a skin makes one)

# a changed setting -> where it lives in the settings, and its allowed range (whole numbers)
NUMBERS: dict[str, tuple[str, str, int, int]] = {
    "throttle_minutes": ("alerts", "throttle_minutes", 1, 1_440),
    "digest_minutes": ("alerts", "digest_minutes", 1, 1_440),
    "watched_max_markets": ("feed", "watched_max_markets", 1, 1_000),
    "read_share_percent": ("feed", "read_share_percent", 1, 100),
    "storage_budget_gb": ("feed", "storage_budget_gb", 1, 100_000),
    "port": ("terminal", "port", 1_024, 65_535),
    "session_days": ("terminal", "session_days", 1, 365),
}
TEXTS: dict[str, tuple[str | None, str]] = {
    "skin": ("terminal", "skin"),
    "quiet_start": ("alerts", "quiet_start"),
    "quiet_end": ("alerts", "quiet_end"),
    "tax_time_zone": (None, "tax_time_zone"),
    "backup_share": ("backup", "share"),
}
# saved settings the running engine reads only when it starts
AT_RESTART = (
    "tax_time_zone", "backup_share", "watched_max_markets", "read_share_percent",
    "storage_budget_gb", "port", "session_days",
)  # fmt: skip


class SettingsRefused(ValueError):
    """A change that cannot be saved (said in plain words)."""


def _value(settings: Settings, name: str) -> Any:
    section, field = TEXTS[name] if name in TEXTS else NUMBERS[name][:2]
    holder = settings if section is None else getattr(settings, section)
    return getattr(holder, field)


def _with(settings: Settings, name: str, value: Any) -> Settings:
    section, field = TEXTS[name] if name in TEXTS else NUMBERS[name][:2]
    if section is None:
        return msgspec.structs.replace(settings, **{field: value})
    part = msgspec.structs.replace(getattr(settings, section), **{field: value})
    return msgspec.structs.replace(settings, **{section: part})


def checked(settings: Settings, change: c.SettingsChange) -> Settings:
    """``settings`` with ``change`` applied, every value checked; raises SettingsRefused."""
    wanted = msgspec.structs.asdict(change)
    for name, value in wanted.items():
        if value is None:
            continue
        if name in NUMBERS:
            low, high = NUMBERS[name][2:]
            if isinstance(value, bool) or not low <= value <= high:
                raise SettingsRefused(f"{name} must be a whole number from {low} to {high}")
        elif name == "skin" and value not in SKINS:
            raise SettingsRefused(f"skin must be one of {', '.join(SKINS)}")
        elif name == "tax_time_zone" and value.strip():
            try:
                taxzone.resolve(value.strip())
            except Exception as exc:  # an unknown zone: said plainly
                raise SettingsRefused(f"{value.strip()!r} is not a time zone IKARS knows") from exc
        elif name == "backup_share" and value.strip() and not Path(value.strip()).is_absolute():
            # a relative path would land under the engine's working folder (merge review 3)
            raise SettingsRefused(r"the share must be a full path, like \\server\share")
        settings = _with(settings, name, value.strip() if isinstance(value, str) else value)
    try:
        QuietHours.parse(settings.alerts.quiet_start, settings.alerts.quiet_end)
    except ValueError as exc:
        raise SettingsRefused("quiet hours must be times like 22:00") from exc
    return settings


def user_environment(names: tuple[str, ...]) -> dict[str, str]:
    """These variables as Windows has them for the user now (the engine's own environment is fixed
    when it starts: a variable set since then shows only here). Empty off Windows."""
    if sys.platform != "win32":
        return {}
    import winreg

    found: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            for name in names:
                try:
                    value, kind = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                # an expandable value (%USERPROFILE%\...) reads as Windows gives it to programs
                expand = kind == winreg.REG_EXPAND_SZ
                found[name] = os.path.expandvars(value) if expand else str(value)
    except OSError:
        return {}
    return found


def key_status(
    settings: Settings,
    environ: dict[str, str] | None = None,
    windows: dict[str, str] | None = None,
) -> c.KeyStatus:
    """The key as yes or no: from this engine's environment, or else from what Windows has now (then
    a restart is needed to use it; merge review 6)."""
    names = (settings.kalshi.key_id_variable, settings.kalshi.key_path_variable)
    own = dict(os.environ) if environ is None else environ
    now = user_environment(names) if windows is None else windows
    needs_restart = any(not own.get(n) and now.get(n) for n in names)
    env = {n: own.get(n) or now.get(n, "") for n in names}
    path = env.get(settings.kalshi.key_path_variable, "")
    found = bool(path) and Path(path).is_file()
    owner_only: bool | None = None
    if found:
        try:
            owner_only = not other_readers(Path(path))
        except (OSError, NotImplementedError):
            owner_only = None
    return c.KeyStatus(
        id_set=bool(env.get(settings.kalshi.key_id_variable)), path_set=bool(path),
        file_found=found, owner_only=owner_only, environment=settings.kalshi.environment,
        needs_restart=needs_restart,
    )  # fmt: skip


def check_share(share: str) -> c.ShareCheckView:
    """Whether the share answers and holds IKARS's folder and marker; a test file is written (and
    removed) only inside that folder, never elsewhere on the share (PLAN D18)."""
    try:
        place = Share(share)
    except ShareError as exc:
        return c.ShareCheckView(reachable=False, marker=False, writable=None, problem=str(exc))
    if not place.base.is_dir():
        return c.ShareCheckView(reachable=False, marker=False, writable=None,
                                problem="the share is not reachable")  # fmt: skip
    marker = (place.root / MARKER).is_file()
    writable: bool | None = None
    if place.root.is_dir():
        probe = place.root / f".write-test-{secrets.token_hex(4)}"
        try:
            probe.write_text("IKARS write test\n", encoding="utf-8")
            probe.unlink()
            writable = True
        except OSError:
            writable = False
    problem = None if marker and writable else place.problem() or "IKARS cannot write there"
    return c.ShareCheckView(reachable=True, marker=marker, writable=writable, problem=problem)


@dataclass
class TerminalConfig:
    """The settings and actions for one engine: its folders, the settings it runs with, its root."""

    paths: Paths
    running: Settings
    root: Path | None = None  # the engine's --root, for a restart or the start at sign-in
    registry: autostart.Registry | None = None  # the Windows registry (tests give MemoryRegistry)
    run_command: Callable[[list[str]], int] = autostart.run_quietly
    # it runs under ``serve`` (a foreground ``run`` cannot restart itself)
    restartable: bool = False
    # reads and writes of the settings file take turns: two changes at once (a skin hotkey while
    # Settings saves) collided on the same temporary file, and Windows cannot replace a file a read
    # holds open (CI's first run on main)
    _turn: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _saved(self) -> Settings:
        """The settings on disk (the running ones before the first save); a file that cannot be read
        is refused in words, never a crash, and never overwritten."""
        if not self.paths.settings.exists():
            return self.running
        try:
            return load(self.paths.settings)
        except SettingsError as exc:
            raise SettingsRefused(f"the settings file cannot be read: {exc}") from exc

    def view(self) -> c.SettingsView:
        with self._turn:
            saved = self._saved()
        try:
            zone = taxzone.resolve(saved.tax_time_zone)
        except Exception as exc:  # a hand-edited zone IKARS does not know: said, never a 500
            raise SettingsRefused(f"the settings' tax time zone cannot be used: {exc}") from exc
        registry = self.registry if self.registry is not None else autostart.UserRegistry()
        try:
            installed = all(autostart.installed(registry, self.run_command).values())
        except OSError:
            installed = False
        return c.SettingsView(
            first_run=not self.paths.settings.exists(),
            skin=saved.terminal.skin,
            quiet_start=saved.alerts.quiet_start, quiet_end=saved.alerts.quiet_end,
            throttle_minutes=saved.alerts.throttle_minutes,
            digest_minutes=saved.alerts.digest_minutes,
            tax_time_zone=saved.tax_time_zone, tax_zone_effective=zone.name,
            backup_share=saved.backup.share,
            watched_max_markets=saved.feed.watched_max_markets,
            read_share_percent=saved.feed.read_share_percent,
            storage_budget_gb=saved.feed.storage_budget_gb,
            port=saved.terminal.port, session_days=saved.terminal.session_days,
            restart_needed=[n for n in AT_RESTART if _value(saved, n) != _value(self.running, n)],
            key=key_status(saved), autostart_installed=installed,
        )  # fmt: skip

    def change(self, change: c.SettingsChange) -> c.SettingsView:
        """Check and save ``change`` (the old file kept as a backup first)."""
        with self._turn:
            updated = checked(self._saved(), change)
            only_skin = all(
                v is None for k, v in msgspec.structs.asdict(change).items() if k != "skin"
            )
            if self.paths.settings.exists() and not only_skin:  # a skin is worth no backup
                backup(self.paths.settings)
                self._prune_backups()
            save(updated, self.paths.settings)
        return self.view()

    def _prune_backups(self) -> None:
        """The latest BACKUPS_KEPT backups of the settings file (merge review 4)."""
        name = self.paths.settings
        kept = sorted(name.parent.glob(f"{name.stem}.*.bak{name.suffix}"))
        for old in kept[:-BACKUPS_KEPT]:
            old.unlink(missing_ok=True)

    async def share_check(self, share: str) -> c.ShareCheckView:
        return await asyncio.wait_for(in_daemon_thread(lambda: check_share(share)), SHARE_WAIT_S)

    async def share_init(self, share: str, now_ns: int) -> c.ShareCheckView:
        """Create IKARS's own folder and marker on the share (nothing else), then check it."""

        def init() -> c.ShareCheckView:
            try:
                Share(share).init(now_ns)
            except (ShareError, OSError) as exc:
                return c.ShareCheckView(
                    reachable=False, marker=False, writable=None, problem=str(exc)
                )
            return check_share(share)

        return await asyncio.wait_for(in_daemon_thread(init), SHARE_WAIT_S)

    def install_autostart(self) -> c.Done:
        registry = self.registry if self.registry is not None else autostart.UserRegistry()
        done = autostart.install(
            registry, self.run_command, autostart.windowless_python(), self.root
        )
        if all(done.values()):
            return c.Done(done=True, detail="the engine starts at sign-in, with its watchdog")
        missing = ", ".join(name for name, ok in done.items() if not ok)
        return c.Done(done=False, detail=f"not all installed: {missing}")

    def restart(self) -> c.Done:
        """Ask the engine to stop and start afresh (not marked as stopped by the owner)."""
        if not self.restartable:  # merge review 2: nothing would start it again
            return c.Done(
                done=False,
                detail="this engine runs in the foreground: stop it (Ctrl+C) and start it again",
            )
        self.paths.data.mkdir(parents=True, exist_ok=True)
        (self.paths.data / RESTART).write_text("restart\n", encoding="utf-8")
        (self.paths.data / runmode.STOP).write_text("restart\n", encoding="utf-8")
        return c.Done(done=True, detail="the engine stops and starts again (about half a minute)")


def restart_wanted(folder: Path) -> bool:
    """A restart was asked for: the marker is spent here (the caller starts the new engine)."""
    marker = folder / RESTART
    if not marker.exists():
        return False
    marker.unlink(missing_ok=True)
    return True
