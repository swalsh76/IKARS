"""Auto-start, the watchdog's schedule and the notification name (PLAN D19 G5), all user space.

``install-autostart`` writes three things, and ``remove-autostart`` removes all three:

- ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` value ``IKARS Engine``: starts
  ``pythonw -m ikars_engine serve`` at sign-in (no admin rights: a logon-triggered scheduled task
  usually needs them, and this workstation's session has none);
- the scheduled task ``IKARS Watchdog``: ``pythonw -m ikars_engine watchdog`` every 5 minutes
  (owner, 2026-10-27), as the signed-in user, while signed in (a time trigger needs no admin
  rights);
- ``HKCU\\Software\\Classes\\AppUserModelId\\IKARS``: the name Windows shows on IKARS's
  notifications.

Every Windows call goes through a small interface, so the tests use fakes.
"""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "IKARS Engine"
TASK = "IKARS Watchdog"
APP_KEY = r"Software\Classes\AppUserModelId\IKARS"
EVERY_MINUTES = 5


class Registry(Protocol):
    def set(self, key: str, name: str, value: str) -> None: ...
    def delete_value(self, key: str, name: str) -> bool: ...
    def delete_key(self, key: str) -> bool: ...
    def get(self, key: str, name: str) -> str | None: ...


class UserRegistry:
    """``HKEY_CURRENT_USER`` through ``winreg``."""

    def set(self, key: str, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete_value(self, key: str, name: str) -> bool:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            return False
        return True

    def delete_key(self, key: str) -> bool:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            return False
        return True

    def get(self, key: str, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                value, _ = winreg.QueryValueEx(handle, name)
        except FileNotFoundError:
            return None
        return str(value)


Run = Callable[[list[str]], int]  # runs a command, returns its exit code


class MemoryRegistry:
    """The registry in memory: for tests and the made-up-data server, never the real one."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def set(self, key: str, name: str, value: str) -> None:
        self.values[(key, name)] = value

    def delete_value(self, key: str, name: str) -> bool:
        return self.values.pop((key, name), None) is not None

    def delete_key(self, key: str) -> bool:
        found = [k for k in self.values if k[0] == key]
        for k in found:
            del self.values[k]
        return bool(found)

    def get(self, key: str, name: str) -> str | None:
        return self.values.get((key, name))


def run_quietly(argv: list[str]) -> int:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    done = subprocess.run(  # noqa: S603 - fixed arguments (schtasks), no shell
        argv, capture_output=True, timeout=60, creationflags=flags, check=False
    )
    return done.returncode


def windowless_python() -> str:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate if candidate.exists() else Path(sys.executable))


def _quoted(command: list[str]) -> str:
    return " ".join(f'"{part}"' if " " in part else part for part in command)


def install(registry: Registry, run: Run, python: str, root: Path | None = None) -> dict[str, bool]:
    """``root``: a data folder other than the usual, passed to both the engine and the watchdog,
    so the watchdog looks where the engine runs."""
    rooted = ["--root", str(root)] if root is not None else []
    engine = _quoted([python, "-m", "ikars_engine", "serve", *rooted])
    registry.set(RUN_KEY, RUN_VALUE, engine)
    registry.set(APP_KEY, "DisplayName", "IKARS")
    watchdog = _quoted([python, "-m", "ikars_engine", "watchdog", *rooted])
    task = run(["schtasks", "/Create", "/F", "/TN", TASK, "/SC", "MINUTE",
                "/MO", str(EVERY_MINUTES), "/TR", watchdog])  # fmt: skip
    return {"autostart": True, "notification_name": True, "watchdog_task": task == 0}


def remove(registry: Registry, run: Run) -> dict[str, bool]:
    return {
        "autostart": registry.delete_value(RUN_KEY, RUN_VALUE),
        "notification_name": registry.delete_key(APP_KEY),
        "watchdog_task": run(["schtasks", "/Delete", "/F", "/TN", TASK]) == 0,
    }


def installed(registry: Registry, run: Run) -> dict[str, bool]:
    return {
        "autostart": registry.get(RUN_KEY, RUN_VALUE) is not None,
        "notification_name": registry.get(APP_KEY, "DisplayName") is not None,
        "watchdog_task": run(["schtasks", "/Query", "/TN", TASK]) == 0,
    }
