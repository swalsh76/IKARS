"""Alerts on the workstation (PLAN D19 G6 as amended, D20 H): no phone pushes (owner, 2026-10-27).

**Priorities.** ``urgent`` (the engine is down, the ledger's chain is broken, backups are failing),
``high``, ``normal`` and ``low``. Outside quiet hours, urgent, high and normal alerts pop up as a
Windows notification; low ones wait for the **hourly digest**. In **quiet hours** (a setting; owner:
22:00 to 07:00) only urgent alerts pop up, and everything else waits for the first digest after.
(Normal pops up from M1.12c, for the strongest recommendations; nothing raised it before.)

**De-duplication and throttling.** An alert has a key; the same key does not pop up again within the
throttle interval (it is still written to the log).

**The alert log** (``alerts.jsonl`` in the data folder) records every alert, shown or held, so an
alert dismissed or missed is never lost; the terminal shows it from M1.8. Two processes write it
(the watchdog and, from M1.12c, the engine), each line under a lock they share; each keeps its own
desk state, since a watchdog pass rewrites its own file whole.

**The notifier** shows a Windows notification ("toast") through Windows' own notification classes,
run by Windows PowerShell, so nothing is installed and nothing leaves the machine. The title and
text reach the script through environment variables and are XML-escaped there, never pasted into
code. IKARS's notification name (``IKARS``) is registered under ``HKCU`` by ``install-autostart``,
so the toast says IKARS and Windows' own settings (Do Not Disturb, muting an app) apply to it.

Alerts carry status text only: never an amount, a key or a path.
"""

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Protocol

from ikars_engine.files import write_text_atomic

URGENT, HIGH, NORMAL, LOW = "urgent", "high", "normal", "low"
PRIORITIES = (URGENT, HIGH, NORMAL, LOW)
APP_ID = "IKARS"
ALERT_LOG = "alerts.jsonl"  # in the data folder; the API serves it
DESK_STATE = "alert-desk.json"  # the watchdog's; the engine keeps its own (M1.12c)
NS = 1_000_000_000

_WINRT = ", ContentType = WindowsRuntime] | Out-Null"
TOAST_SCRIPT = (
    (
        "$ErrorActionPreference = 'Stop'\n"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications"
        + _WINRT
        + "\n"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument" + _WINRT + "\n"
    )
    + r"""
$title = [Security.SecurityElement]::Escape($env:IKARS_TOAST_TITLE)
$text = [Security.SecurityElement]::Escape($env:IKARS_TOAST_TEXT)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$body = "<text>$title</text><text>$text</text>"
$xml.LoadXml("<toast><visual><binding template=`"ToastGeneric`">$body</binding></visual></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:IKARS_TOAST_APP).Show($toast)
"""
)


@dataclass(frozen=True, slots=True)
class Alert:
    key: str  # what it is about; the same key is de-duplicated and throttled
    priority: str
    title: str
    text: str


class Notifier(Protocol):
    def show(self, title: str, text: str, urgent: bool) -> None: ...


class NotificationFailed(RuntimeError):
    pass


class ToastNotifier:
    """A Windows notification, through Windows PowerShell's WinRT classes."""

    def __init__(self, app_id: str = APP_ID, timeout_s: float = 20.0) -> None:
        self._app_id = app_id
        self._timeout = timeout_s

    def show(self, title: str, text: str, urgent: bool) -> None:
        env = {**os.environ, "IKARS_TOAST_APP": self._app_id, "IKARS_TOAST_TITLE": title,
               "IKARS_TOAST_TEXT": text, "IKARS_TOAST_URGENT": "1" if urgent else "0"}  # fmt: skip
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            done = subprocess.run(  # noqa: S603 - a fixed script; the text goes in through env
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", TOAST_SCRIPT],  # noqa: S607 - Windows' own
                env=env,
                capture_output=True,
                timeout=self._timeout,
                creationflags=flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NotificationFailed(type(exc).__name__) from None
        if done.returncode != 0:
            raise NotificationFailed(f"powershell exited {done.returncode}")


@dataclass(frozen=True, slots=True)
class QuietHours:
    start: time
    end: time

    @classmethod
    def parse(cls, start: str, end: str) -> "QuietHours":
        return cls(time.fromisoformat(start), time.fromisoformat(end))

    def contains(self, moment: datetime) -> bool:
        now = moment.time()
        if self.start <= self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end  # across midnight


@dataclass
class DeskState:
    """What the desk remembers between runs (the watchdog is a new process each time)."""

    shown_ns: dict[str, int] = field(default_factory=dict)  # key -> when it last popped up
    held: list[dict[str, str]] = field(default_factory=list)  # waiting for the digest
    digest_ns: int = 0


class AlertDesk:
    def __init__(
        self,
        notifier: Notifier,
        folder: Path,
        quiet: QuietHours,
        *,
        throttle_s: int = 600,
        digest_s: int = 3_600,
        state_name: str = DESK_STATE,
    ) -> None:
        self._notifier = notifier
        self._log = folder / ALERT_LOG
        self._state_path = folder / state_name
        self._quiet = quiet
        self._throttle_ns = throttle_s * NS
        self._digest_ns = digest_s * NS
        self.state = self._load()

    def raise_alert(self, alert: Alert, now: datetime) -> str:
        """Log ``alert`` and show it, hold it or throttle it; returns which. A notification that
        cannot be shown raises :class:`NotificationFailed` (the caller tries again later)."""
        if alert.priority not in PRIORITIES:
            raise ValueError(f"no such priority {alert.priority!r}")
        at = _ns(now)
        quiet = self._quiet.contains(now)
        pops = alert.priority == URGENT or (alert.priority in (HIGH, NORMAL) and not quiet)
        last = self.state.shown_ns.get(alert.key)
        if pops and last is not None and at - last < self._throttle_ns:
            outcome = "throttled"
        elif pops:
            try:
                self._notifier.show(f"IKARS: {alert.title}", alert.text, alert.priority == URGENT)
            except NotificationFailed:  # in the log all the same; the caller tries again
                self._append({**asdict(alert), "at": now.isoformat(), "outcome": "failed"})
                raise
            self.state.shown_ns[alert.key] = at
            outcome = "shown"
        else:
            self.state.held.append({"title": alert.title, "priority": alert.priority})
            outcome = "held"
        self._append({**asdict(alert), "at": now.isoformat(), "outcome": outcome})
        self._save()
        return outcome

    def quiet(self, now: datetime) -> bool:
        return self._quiet.contains(now)

    def digest(self, now: datetime) -> bool:
        """Show the held alerts as one notification, when the hour is up and it is not quiet."""
        at = _ns(now)
        too_soon = at - self.state.digest_ns < self._digest_ns
        if not self.state.held or self._quiet.contains(now) or too_soon:
            return False
        held = self.state.held
        titles = "; ".join(item["title"] for item in held[:3]) + ("; ..." if len(held) > 3 else "")
        self._notifier.show(f"IKARS: {len(held)} alerts", titles, False)
        self.state.held, self.state.digest_ns = [], at
        self._append({"key": "digest", "priority": LOW, "title": f"{len(held)} alerts",
                      "text": titles, "at": now.isoformat(), "outcome": "shown"})  # fmt: skip
        self._save()
        return True

    def _append(self, entry: dict[str, object]) -> None:
        self._log.parent.mkdir(parents=True, exist_ok=True)
        lock = self._log.with_name(ALERT_LOG + ".lock")
        with _locked(lock), open(self._log, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _load(self) -> DeskState:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            return DeskState(dict(raw["shown_ns"]), list(raw["held"]), int(raw["digest_ns"]))
        except (OSError, ValueError, KeyError, TypeError):
            return DeskState()  # a damaged file must never stop the alerts

    def _save(self) -> None:
        write_text_atomic(self._state_path, json.dumps(asdict(self.state)))


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Holds ``path``'s lock (waiting up to about 10 seconds), so a line from the watchdog and one
    from the engine are never interleaved in the alert log."""
    with open(path, "a+b") as handle:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)  # retries for 10 s, then raises
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover - IKARS targets Windows; kept so the tests run anywhere
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000) * 1_000
