"""Commands for the background engine, the watchdog and alerts (M1.6). Each takes ``--root``.

- ``serve``: the engine in the background until ``stop`` (one instance; the system kept awake).
- ``stop``: ask the engine to stop, and wait for it (up to a minute).
- ``status``: whether it runs, how fresh its snapshot is, and what is installed (no amounts).
- ``watchdog``: one watchdog pass (the scheduled task runs this every 5 minutes).
- ``install-autostart`` / ``remove-autostart``: the sign-in start, the watchdog's task, and the
  notification name, all user space.
- ``alerts test``: show one test notification.
- ``open``: the web terminal in its own Edge window (starting the engine if needed), or a saved
  layout's windows (``--layout``).
- ``support``: a redacted support bundle in the data folder's ``support`` folder (never sent).
- ``rollback VERSION``: back to an earlier release, its pre-upgrade snapshot restored (M1.9b).
"""

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ikars_engine import autostart, runmode
from ikars_engine.alerts import (
    DESK_STATE,
    URGENT,
    Alert,
    AlertDesk,
    NotificationFailed,
    QuietHours,
    ToastNotifier,
)
from ikars_engine.api import engine_post, read_api_file
from ikars_engine.contracts import Done
from ikars_engine.logs import Redactor, configure
from ikars_engine.paths import Paths
from ikars_engine.settings import Settings
from ikars_engine.settings_api import restart_wanted
from ikars_engine.watchdog import check, engine_command, start_engine

log = logging.getLogger("ikars_engine.serve")
COMMANDS = (
    "serve",
    "stop",
    "status",
    "watchdog",
    "install-autostart",
    "remove-autostart",
    "alerts",
    "open",
    "support",
    "rollback",
)
STOP_WAIT_S = 60


def add_parsers(commands: Any) -> None:
    helps = {
        "serve": "the engine in the background until stopped",
        "stop": "stop the background engine cleanly",
        "status": "whether the engine runs, and what is installed",
        "watchdog": "one watchdog pass (run by the scheduled task)",
        "install-autostart": "start at sign-in, the watchdog task and the notification name",
        "remove-autostart": "remove all three",
        "open": "the web terminal in its own Edge window",
        "support": "a redacted support bundle in the data folder (never sent)",
    }
    for name, text in helps.items():
        parser = commands.add_parser(name, help=text)
        parser.add_argument("--root", default=None, help="the data folder (default: the usual)")
        if name == "open":
            parser.add_argument(
                "--layout", default=None,
                help="a saved layout's windows (default: the one chosen to open at start; '': one)",
            )  # fmt: skip
    back = commands.add_parser("rollback", help="back to an earlier release and its data (M1.9b)")
    back.add_argument("--root", default=None, help="the data folder (default: the usual)")
    back.add_argument("version", help="the release to go back to, like 0.1.0")
    alerts = commands.add_parser("alerts", help="workstation notifications")
    alerts.add_argument("--root", default=None, help="the data folder (default: the usual)")
    alerts.add_argument("action", choices=["test"])


def rollback(paths: Paths, version: str, managers: Any = None) -> dict[str, object]:
    """Back to release ``version``: everything is checked and the release downloaded before the
    engine stops and the data is swapped (a failed swap is undone), so a failure on the way leaves
    the current version and its data as they were."""
    from ikars_engine import launcher, updates, upgrades

    where = updates.locator() if managers is None else managers.where
    if where is None:
        raise upgrades.RollbackRefused("only the installed program rolls back")
    make = updates.github_manager if managers is None else managers.current
    current = str(make(where).get_current_version())
    if version == current:
        raise upgrades.RollbackRefused(f"{version} is the version installed now")
    upgrades.require_snapshot(paths, version)
    back = (updates.downgrade_manager if managers is None else managers.back)(version, where)
    try:
        found_release = back.check_for_updates()
    except Exception as exc:
        raise upgrades.RollbackRefused(f"release v{version} could not be read: {exc}") from exc
    if found_release is None or str(found_release.TargetFullRelease.Version) != version:
        raise upgrades.RollbackRefused(f"release v{version} was not found on GitHub")
    back.download_updates(found_release)
    launcher.stop_engine(paths)
    if runmode.engine_running(paths.data):  # never a swap under a running engine (merge review 2)
        raise upgrades.RollbackRefused(
            "the engine did not stop in time: nothing changed, try again"
        )
    done = upgrades.restore(paths, version, current)
    back.wait_exit_then_apply_updates(found_release, silent=True, restart=True)
    return {"rolled_back": True, "installing": version, **done}


def support_bundle(paths: Paths, settings: Settings) -> Done:
    """Asks the running engine for a bundle (it adds its health and the account's amounts to what
    is redacted); makes one here when no engine answers."""
    from ikars_engine import support

    # the token is redacted even when the engine is stopped: it outlives a restart (merge review 3)
    record = read_api_file(paths.data)
    token = str(record.get("token", "")) if record else None
    if runmode.engine_running(paths.data):  # an engine starting or stopping: made here
        done = engine_post(paths.data, "/v1/support/bundle", 120)
        if done is not None:
            return done
    try:
        return support.make(paths, settings, dict(os.environ), token=token)
    except (support.BundleRefused, OSError) as exc:
        return Done(done=False, detail=str(exc))


def desk_for(paths: Paths, settings: Settings, state_name: str = DESK_STATE) -> AlertDesk:
    try:
        quiet = QuietHours.parse(settings.alerts.quiet_start, settings.alerts.quiet_end)
    except ValueError:  # a bad setting must never stop the watchdog: the owner's default, logged
        log.warning("alerts.quiet_start or quiet_end is not a time; using 22:00 to 07:00")
        quiet = QuietHours.parse("22:00", "07:00")
    return AlertDesk(
        ToastNotifier(),
        paths.data,
        quiet,
        throttle_s=settings.alerts.throttle_minutes * 60,
        digest_s=settings.alerts.digest_minutes * 60,
        state_name=state_name,
    )


def run(args: argparse.Namespace, paths: Paths, settings: Settings) -> int:
    root = Path(args.root).resolve() if args.root else None  # later runs start elsewhere
    if args.command == "serve":
        return serve(paths, settings, root)
    if args.command == "stop":
        return stop(paths)
    if args.command == "status":
        _print(status(paths))
        return 0
    if args.command == "watchdog":
        configure(paths.logs, Redactor.from_environment([]), filename="watchdog.jsonl")
        did = check(paths.data, desk_for(paths, settings), datetime.now().astimezone(),
                    command=engine_command(root))  # fmt: skip
        _print(did)
        return 0
    if args.command == "install-autostart":
        registry, python = autostart.UserRegistry(), autostart.windowless_python()
        _print(autostart.install(registry, autostart.run_quietly, python, root))
        return 0
    if args.command == "remove-autostart":
        _print(autostart.remove(autostart.UserRegistry(), autostart.run_quietly))
        return 0
    if args.command == "open":
        from ikars_engine.open_terminal import OpenError, open_terminal

        try:
            _print(open_terminal(paths, root, settings=settings, layout=args.layout))
        except OpenError as exc:
            _print({"opened": False, "problem": str(exc)})
            return 1
        return 0
    if args.command == "rollback":
        from ikars_engine.upgrades import RollbackRefused

        try:
            _print(rollback(paths, args.version))
        except RollbackRefused as exc:
            _print({"rolled_back": False, "problem": str(exc)})
            return 1
        return 0
    if args.command == "support":
        done = support_bundle(paths, settings)
        _print({"done": done.done, "detail": done.detail})
        return 0 if done.done else 1
    try:
        outcome = desk_for(paths, settings).raise_alert(
            Alert("test", URGENT, "test", "IKARS notifications work on this workstation"),
            datetime.now().astimezone(),
        )
    except NotificationFailed as exc:
        _print({"shown": False, "problem": str(exc)})
        return 1
    _print({"shown": outcome == "shown", "outcome": outcome})
    return 0


def serve(paths: Paths, settings: Settings, root: Path | None = None) -> int:
    from ikars_engine.kalshi.runner import run as run_engine

    paths.create()
    lock = runmode.EngineLock(paths.data)
    try:
        lock.acquire(wait_s=3.0)
    except runmode.AlreadyRunning:
        _print({"refused": "the engine is already running"})
        return 1
    try:
        runmode.clear_stop(paths.data)
        runmode.write_snapshot(paths.data, runmode.starting_snapshot(time.time_ns()))

        def snapshot(health: dict[str, object]) -> None:
            try:
                runmode.write_snapshot(paths.data, runmode.snapshot_of(health, time.time_ns()))
            except OSError as exc:  # a reader held it this instant: the next minute writes it
                log.warning("health snapshot not written", extra={"error": type(exc).__name__})

        def waiting(seconds: int | None) -> None:  # still starting: the watchdog keeps its grace
            try:
                runmode.write_snapshot(
                    paths.data, runmode.starting_snapshot(time.time_ns(), waiting_s=seconds)
                )
            except OSError as exc:
                log.warning("health snapshot not written", extra={"error": type(exc).__name__})

        with runmode.keep_awake():
            asyncio.run(
                run_engine(
                    paths,
                    settings,
                    None,
                    out=lambda line: None,
                    should_stop=lambda: runmode.stop_requested(paths.data),
                    on_minute=snapshot,
                    root=root,
                    on_wait=waiting,
                )
            )
    except Exception as exc:  # in the background nobody sees a traceback: it goes to the log
        log.error("the engine stopped on an error", extra={"error": f"{type(exc).__name__}: {exc}"})
        _print({"stopped_on_error": type(exc).__name__})
        return 1
    finally:
        (paths.data / runmode.STOP).unlink(missing_ok=True)
        if restart_wanted(paths.data):
            # Settings' "Restart the engine": the fresh one starts while this one still holds the
            # lock (it waits for it), so the watchdog never sees a gap to fill (merge review 8)
            start_engine(engine_command(root))
        lock.release()
    return 0


def stop(paths: Paths) -> int:
    if not runmode.engine_running(paths.data):
        runmode.request_stop(paths.data)  # still marks it stopped by the owner, for the watchdog
        (paths.data / runmode.STOP).unlink(missing_ok=True)
        _print({"stopped": True, "was_running": False})
        return 0
    runmode.request_stop(paths.data)
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline and runmode.engine_running(paths.data):
        time.sleep(1)
    stopped = not runmode.engine_running(paths.data)
    _print({"stopped": stopped, "was_running": True})
    return 0 if stopped else 1


def status(paths: Paths) -> dict[str, object]:
    snap = runmode.read_snapshot(paths.data)
    written = runmode.at(snap, "written_ns")
    api = read_api_file(paths.data)
    return {
        "running": runmode.engine_running(paths.data),
        "api_port": None if api is None else api.get("port"),  # never the token
        "stopped_by_owner": runmode.stopped_by_owner(paths.data),
        "snapshot_age_s": (
            None if not isinstance(written, int) else (time.time_ns() - written) // 1_000_000_000
        ),
        "snapshot": snap,
        "installed": autostart.installed(autostart.UserRegistry(), autostart.run_quietly),
    }  # fmt: skip


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))
