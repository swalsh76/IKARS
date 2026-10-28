"""The watchdog (PLAN D19 G5): restarts the engine and tells the owner, on the workstation.

``python -m ikars_engine watchdog`` runs once, every 5 minutes, from a per-user scheduled task
(owner, 2026-10-27), in a process of its own so that it can report on an engine that is dead. It
reads the engine's lock and its health snapshot, restarts an engine that died (unless the owner
stopped it; at most ``restart_limit`` times an hour), and raises alerts through the alert desk
(Windows notifications, the alert log):

* when a problem has held for ``confirm_checks`` checks in a row (one blip never alerts);
* again every ``reminder_hours`` while it lasts; and
* once when it clears.

*Kept from KAPS's watchdog:* those three rules, the state file of what has been reported (so it
never repeats itself), and a notification that cannot be shown not counting as shown (it is tried
again at the next check). *Changed:* the delivery (Windows notifications, not ntfy: the owner wants
no phone pushes), the observations (a lock and a snapshot, not ``systemd`` and PostgreSQL), and the
restart, which ``systemd`` did for KAPS.
"""

import contextlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ikars_engine.alerts import HIGH, URGENT, Alert, AlertDesk, NotificationFailed
from ikars_engine.autostart import windowless_python
from ikars_engine.files import write_text_atomic
from ikars_engine.runmode import at, engine_running, read_snapshot, stopped_by_owner

log = logging.getLogger("ikars_engine.watchdog")
STATE = "watchdog.json"
TITLES = {"problem": "problem", "reminder": "still a problem", "recovered": "recovered"}
NS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Rules:
    confirm_checks: int = 2
    reminder_hours: int = 6
    restart_limit: int = 3  # an hour
    snapshot_stale_s: int = 180  # the engine writes one a minute
    start_grace_s: int = 600  # a starting engine's first snapshot comes after discovery
    feed_stale_s: int = 600  # while the exchange is trading
    backup_late_hours: int = 36


RULES = Rules()


@dataclass(frozen=True, slots=True)
class Observation:
    running: bool
    stopped_by_owner: bool
    snapshot: dict[str, Any] | None
    now_ns: int


@dataclass(frozen=True, slots=True)
class Problem:
    name: str
    text: str
    urgent: bool = False


def evaluate(obs: Observation, rules: Rules) -> list[Problem]:
    if not obs.running:
        if obs.stopped_by_owner:
            return []  # stopped on purpose: nothing to report about it
        return [Problem("engine", "the engine is not running", urgent=True)]
    snap = obs.snapshot
    written = at(snap, "written_ns")
    starting = at(snap, "starting") is True
    stale_s = rules.start_grace_s if starting else rules.snapshot_stale_s
    if not isinstance(written, int) or obs.now_ns - written > stale_s * NS:
        silent = "the engine is running but has stopped reporting"
        return [Problem("engine-silent", silent, urgent=True)]
    if starting:
        return []  # nothing else to judge until its first real snapshot
    problems = []
    silence = at(snap, "feed", "seconds_since_last_frame")
    if (
        at(snap, "trading_active")
        and isinstance(silence, (int, float))
        and silence > rules.feed_stale_s
    ):
        minutes = int(silence // 60)
        problems.append(Problem("feed", f"no Kalshi frame for {minutes} min while trading is open"))
    if at(snap, "ledger", "verified") is False:
        broken = "the ledger's hash chain is broken: nothing is appended"
        problems.append(Problem("ledger-chain", broken, urgent=True))
    if at(snap, "ledger", "reconciled_exact") is False:
        differs = "the ledger does not reconcile exactly with Kalshi"
        problems.append(Problem("ledger-reconcile", differs))
    if at(snap, "backup", "configured"):
        hours = at(snap, "backup", "hours_since")
        if at(snap, "backup", "last_error") is not None:
            problems.append(Problem("backup", "backups to the share are failing", urgent=True))
        elif isinstance(hours, int) and hours > rules.backup_late_hours:
            problems.append(Problem("backup", f"the last backup is {hours} hours old", urgent=True))
    if at(snap, "storage_state") in ("warn", "pruned"):
        problems.append(Problem("storage", "recordings are near their storage budget"))
    if at(snap, "setup") is not None:  # no usable key: the terminal works, nothing else (M1.9a)
        unset = "the Kalshi key is not set up: set it, then restart the engine (Settings)"
        problems.append(Problem("setup", unset))
    if at(snap, "port_fallback") is True:  # an open terminal cannot find the engine again
        taken = "the terminal's port is taken: reopen it with ikars open, or change the port"
        problems.append(Problem("terminal-port", taken))
    return problems


# --- what has been told (from KAPS) -------------------------------------------------------------


@dataclass
class ProblemState:
    seen: int = 0  # consecutive checks that showed it
    alerted_ns: int | None = None
    last_sent_ns: int | None = None
    text: str = ""
    urgent: bool = False


@dataclass
class State:
    problems: dict[str, ProblemState] = field(default_factory=dict)
    restarts_ns: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Message:
    kind: str  # problem, reminder, recovered
    name: str
    text: str
    urgent: bool = False


def plan(state: State, current: list[Problem], now_ns: int, rules: Rules) -> list[Message]:
    """The messages due now. Only counts change here; :func:`committed` records a message once it
    has really been shown, so a failed notification is simply due again at the next check."""
    messages: list[Message] = []
    present = {p.name: p for p in current}
    for name, problem in present.items():
        known = state.problems.setdefault(name, ProblemState())
        known.seen += 1
        known.text, known.urgent = problem.text, problem.urgent
        if known.alerted_ns is None:
            if known.seen >= rules.confirm_checks:
                messages.append(Message("problem", name, problem.text, problem.urgent))
        elif now_ns - (known.last_sent_ns or known.alerted_ns) >= rules.reminder_hours * 3_600 * NS:
            messages.append(Message("reminder", name, problem.text, problem.urgent))
    for name, known in list(state.problems.items()):
        if name in present:
            continue
        if known.alerted_ns is None:
            del state.problems[name]  # a blip that never reached the owner is forgotten
        else:
            known.seen = 0
            messages.append(Message("recovered", name, f"recovered: {known.text}"))
    return messages


def committed(state: State, message: Message, now_ns: int) -> None:
    known = state.problems[message.name]
    if message.kind == "recovered":
        del state.problems[message.name]
        return
    known.alerted_ns = known.alerted_ns or now_ns
    known.last_sent_ns = now_ns


def load_state(path: Path) -> State:
    """The saved state, or a fresh one (a damaged file must never stop the alerts)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        problems = {k: ProblemState(**v) for k, v in raw["problems"].items()}
        return State(problems, [int(n) for n in raw["restarts_ns"]])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return State()


def save_state(path: Path, state: State) -> None:
    text = json.dumps({"problems": {k: asdict(v) for k, v in state.problems.items()},
                       "restarts_ns": state.restarts_ns})  # fmt: skip
    write_text_atomic(path, text)


# --- restarting ---------------------------------------------------------------------------------


def engine_command(root: Path | None) -> list[str]:
    """``pythonw -m ikars_engine serve`` (no console) from this interpreter's folder."""
    # -I: no PYTHON* variables, no user site and not the working folder on the path (merge review 3)
    command = [windowless_python(), "-I", "-m", "ikars_engine", "serve"]
    return command + (["--root", str(root)] if root is not None else [])


def engine_environment(
    environ: Mapping[str, str] | None = None, windows: Mapping[str, str] | None = None
) -> dict[str, str]:
    """A fresh engine's environment: this process's, with the key's variables as Windows has them
    now. A variable set since this process started (the wizard's key, then Restart the engine)
    must reach the new engine, or it starts in setup mode again (M1.9a)."""
    from ikars_engine.settings import KalshiSettings
    from ikars_engine.settings_api import user_environment

    names = (KalshiSettings().key_id_variable, KalshiSettings().key_path_variable)
    env = dict(os.environ if environ is None else environ)
    now = user_environment(names) if windows is None else windows
    env.update({name: value for name, value in now.items() if value})
    return env


def start_engine(command: list[str]) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(  # noqa: S603 (our own interpreter and module)
        command, creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=os.path.expanduser("~"),
        env=engine_environment(),
    )  # fmt: skip


def check(
    folder: Path,
    desk: AlertDesk,
    now: datetime,
    rules: Rules = RULES,
    start: Any = start_engine,
    command: list[str] | None = None,
) -> dict[str, object]:
    """One watchdog pass; returns what it did (for its log line and the tests)."""
    now_ns = int(now.timestamp()) * NS
    state_path = folder / STATE
    state = load_state(state_path)
    obs = Observation(
        engine_running(folder), stopped_by_owner(folder), read_snapshot(folder), now_ns
    )
    restarted = False
    hour_ago = now_ns - 3_600 * NS
    state.restarts_ns = [t for t in state.restarts_ns if t > hour_ago]
    wanted = not obs.running and not obs.stopped_by_owner
    if wanted and len(state.restarts_ns) < rules.restart_limit:
        start(command or engine_command(None))
        state.restarts_ns.append(now_ns)
        restarted = True
    problems = evaluate(obs, rules)
    if wanted and not restarted:  # the limit is reached: not again until the hour is up
        count = len(state.restarts_ns)
        text = f"the engine was restarted {count} times in an hour; not again"
        problems.append(Problem("restarting", text, urgent=True))
    shown = 0
    if restarted:  # said at once: by the next check the engine is back and the problem gone
        text = "the engine had stopped; the watchdog restarted it"
        with contextlib.suppress(NotificationFailed):
            restart = Alert("watchdog:restarted", HIGH, "engine restarted", text)
            shown += desk.raise_alert(restart, now) == "shown"
    for message in plan(state, problems, now_ns, rules):
        priority = URGENT if message.urgent else HIGH
        title = TITLES[message.kind]
        key = f"watchdog:{message.name}" + (":recovered" if message.kind == "recovered" else "")
        try:
            outcome = desk.raise_alert(Alert(key, priority, title, message.text), now)
        except NotificationFailed as exc:
            log.warning("a notification could not be shown", extra={"error": str(exc)})
            continue
        if outcome == "throttled":
            continue  # not told yet: due again at the next check, after the throttle
        committed(state, message, now_ns)  # shown, or held for the digest and the alert log
        shown += outcome == "shown"
    with contextlib.suppress(NotificationFailed):  # the digest waits for the next check
        desk.digest(now)
    save_state(state_path, state)
    return {
        "running": obs.running,
        "restarted": restarted,
        "problems": len(problems),
        "shown": shown,
    }
