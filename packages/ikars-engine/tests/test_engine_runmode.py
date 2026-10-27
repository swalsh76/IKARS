"""M1.6: the alert desk, the toast notifier, GPU detection, the run-mode files, the watchdog and the
auto-start entries. Windows calls (toasts, the registry, scheduled tasks, nvidia-smi) are faked."""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from ikars_engine import autostart, gpu, runmode
from ikars_engine.alerts import (
    HIGH,
    LOW,
    NORMAL,
    URGENT,
    Alert,
    AlertDesk,
    NotificationFailed,
    QuietHours,
    ToastNotifier,
)
from ikars_engine.watchdog import (
    Observation,
    Problem,
    Rules,
    State,
    check,
    committed,
    engine_command,
    evaluate,
    load_state,
    plan,
)

DENVER = ZoneInfo("America/Denver")
NOON = datetime(2026, 1, 5, 12, 0, tzinfo=DENVER)
NIGHT = datetime(2026, 1, 5, 23, 30, tzinfo=DENVER)
NS = 1_000_000_000
QUIET = QuietHours.parse("22:00", "07:00")


class FakeNotifier:
    def __init__(self, fail: bool = False) -> None:
        self.shown: list[tuple[str, str, bool]] = []
        self.fail = fail

    def show(self, title: str, text: str, urgent: bool) -> None:
        if self.fail:
            raise NotificationFailed("no toast")
        self.shown.append((title, text, urgent))


# --- the alert desk -----------------------------------------------------------------------------


def test_quiet_hours_cross_midnight() -> None:
    assert QUIET.contains(NIGHT) and QUIET.contains(NIGHT + timedelta(hours=6))
    assert not QUIET.contains(NOON) and not QUIET.contains(NIGHT.replace(hour=7, minute=0))
    day = QuietHours.parse("12:00", "13:00")
    assert day.contains(NOON) and not day.contains(NIGHT)


def test_priorities_quiet_hours_throttling_and_the_log(tmp_path: Path) -> None:
    toasts = FakeNotifier()
    desk = AlertDesk(toasts, tmp_path, QUIET)
    stale = Alert("a", HIGH, "feed", "stale")
    assert desk.raise_alert(stale, NOON) == "shown"
    assert desk.raise_alert(stale, NOON + timedelta(minutes=5)) == "throttled"
    assert desk.raise_alert(stale, NOON + timedelta(minutes=11)) == "shown"
    assert desk.raise_alert(Alert("b", NORMAL, "note", "x"), NOON) == "held"
    assert desk.raise_alert(Alert("c", HIGH, "feed", "stale"), NIGHT) == "held"  # quiet: waits
    assert desk.raise_alert(Alert("d", URGENT, "engine down", "x"), NIGHT) == "shown"  # urgent pops
    assert toasts.shown[0] == ("IKARS: feed", "stale", False) and toasts.shown[-1][2] is True
    lines = [
        json.loads(line) for line in (tmp_path / "alerts.jsonl").read_text("utf-8").splitlines()
    ]
    outcomes = [entry["outcome"] for entry in lines]
    assert outcomes == ["shown", "throttled", "shown", "held", "held", "shown"]
    with pytest.raises(ValueError, match="priority"):
        desk.raise_alert(Alert("e", "loud", "x", "y"), NOON)


def test_the_digest_waits_for_the_hour_and_daylight(tmp_path: Path) -> None:
    toasts = FakeNotifier()
    desk = AlertDesk(toasts, tmp_path, QUIET)
    for n in range(4):
        desk.raise_alert(Alert(f"k{n}", LOW, f"item {n}", "x"), NIGHT)
    assert desk.digest(NIGHT) is False  # quiet hours
    morning = NIGHT + timedelta(hours=8)  # 07:30
    assert desk.digest(morning) is True
    assert toasts.shown == [("IKARS: 4 alerts", "item 0; item 1; item 2; ...", False)]
    desk.raise_alert(Alert("k9", LOW, "later", "x"), morning)
    assert desk.digest(morning + timedelta(minutes=30)) is False  # not an hour yet
    again = AlertDesk(toasts, tmp_path, QUIET)  # a new process (the watchdog) remembers
    assert again.state.held == [{"title": "later", "priority": LOW}]
    assert again.digest(morning + timedelta(minutes=61)) is True


def test_a_damaged_desk_state_never_stops_the_alerts(tmp_path: Path) -> None:
    (tmp_path / "alert-desk.json").write_text("{not json", encoding="utf-8")
    desk = AlertDesk(FakeNotifier(), tmp_path, QUIET)
    assert desk.raise_alert(Alert("a", URGENT, "x", "y"), NOON) == "shown"


def test_the_toast_goes_through_powershell_with_the_text_in_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append({"argv": argv, **kwargs})
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr("ikars_engine.alerts.subprocess.run", fake_run)
    ToastNotifier().show("IKARS: <engine>", "it's & down", True)
    env = calls[0]["env"]
    assert calls[0]["argv"][0] == "powershell.exe" and "<engine>" not in calls[0]["argv"][-1]
    assert (env["IKARS_TOAST_TITLE"], env["IKARS_TOAST_TEXT"], env["IKARS_TOAST_APP"]) == (
        "IKARS: <engine>", "it's & down", "IKARS",
    )  # fmt: skip
    assert "SecurityElement]::Escape" in calls[0]["argv"][-1]  # escaped in the script


@pytest.mark.parametrize("outcome", ["exit", "missing", "timeout"])
def test_a_toast_that_fails_says_so(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if outcome == "missing":
            raise FileNotFoundError("powershell")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, 20)
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr("ikars_engine.alerts.subprocess.run", fake_run)
    with pytest.raises(NotificationFailed):
        ToastNotifier().show("t", "x", False)


# --- GPU detection ------------------------------------------------------------------------------

NEW_HEADER = "| NVIDIA-SMI 617.14     KMD Version: 617.14     CUDA UMD Version: 13.4     |"
OLD_HEADER = "| NVIDIA-SMI 570.86   Driver Version: 570.86   CUDA Version: 12.8   |"


def test_gpu_parsing_reads_both_header_styles() -> None:
    cards = gpu.parse("NVIDIA GeForce RTX 5070 Ti, 16303, 617.14\n", NEW_HEADER)
    assert cards == [gpu.Gpu("NVIDIA GeForce RTX 5070 Ti", 16303, "617.14", "13.4")]
    assert gpu.parse("A, 8192, 570.86", OLD_HEADER)[0].cuda == "12.8"
    assert gpu.parse("garbage line\n", "") == []
    assert gpu.health(cards)["device"] == "NVIDIA GeForce RTX 5070 Ti"
    assert gpu.health([]) == {"device": None, "note": "CPU only"}


def test_gpu_detection_without_a_driver_is_cpu_only() -> None:
    def missing(argv: list[str]) -> str:
        raise FileNotFoundError("nvidia-smi")

    assert gpu.detect(missing) == []
    answers = {True: "B, 4096, 1.0", False: OLD_HEADER}
    assert gpu.detect(lambda argv: answers[len(argv) > 1])[0].name == "B"


# --- the run-mode files -------------------------------------------------------------------------


def test_one_engine_at_a_time(tmp_path: Path) -> None:
    first = runmode.EngineLock(tmp_path)
    first.acquire()
    assert runmode.engine_running(tmp_path)
    with pytest.raises(runmode.AlreadyRunning):
        runmode.EngineLock(tmp_path).acquire()
    first.release()
    first.release()  # twice is harmless
    assert not runmode.engine_running(tmp_path)


def test_stop_requests_and_the_snapshot(tmp_path: Path) -> None:
    assert not runmode.stop_requested(tmp_path) and not runmode.stopped_by_owner(tmp_path)
    runmode.request_stop(tmp_path)
    assert runmode.stop_requested(tmp_path) and runmode.stopped_by_owner(tmp_path)
    runmode.clear_stop(tmp_path)
    assert not runmode.stop_requested(tmp_path) and not runmode.stopped_by_owner(tmp_path)
    assert runmode.read_snapshot(tmp_path) is None
    health = {"kalshi": {"feed": {"connected": True, "seconds_since_last_frame": 1.5},
                         "trading_active": True, "open_gaps": 0, "storage": {"state": "ok"}},
              "ledger": {"verified": True, "reconciled_exact": True, "errors": 0},
              "backup": {"configured": True, "hours_since": 3, "last_error": None},
              "gpu": {"device": "card"}}  # fmt: skip
    runmode.write_snapshot(tmp_path, runmode.snapshot_of(health, 42))
    snap = runmode.read_snapshot(tmp_path)
    assert snap is not None and snap["written_ns"] == 42 and snap["gpu"] == "card"
    assert snap["ledger"] == {"verified": True, "reconciled_exact": True, "errors": 0}
    (tmp_path / runmode.SNAPSHOT).write_text("[1]", encoding="utf-8")
    assert runmode.read_snapshot(tmp_path) is None  # not a mapping


def test_keep_awake_asks_windows_and_lets_go() -> None:
    with runmode.keep_awake() as taken:
        assert taken in (True, False)


# --- the watchdog -------------------------------------------------------------------------------

NOW_NS = int(NOON.timestamp()) * NS


def healthy(**changes: Any) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "written_ns": NOW_NS - 30 * NS,
        "feed": {"seconds_since_last_frame": 2.0},
        "trading_active": True,
        "storage_state": "ok",
        "ledger": {"verified": True, "reconciled_exact": True},
        "backup": {"configured": True, "hours_since": 5, "last_error": None},
    }
    snap.update(changes)
    return snap


def names(obs: Observation) -> list[str]:
    return [p.name for p in evaluate(obs, Rules())]


def test_what_the_watchdog_calls_a_problem() -> None:
    assert names(Observation(True, False, healthy(), NOW_NS)) == []
    assert names(Observation(False, False, None, NOW_NS)) == ["engine"]
    assert names(Observation(False, True, None, NOW_NS)) == []  # the owner stopped it
    silent = healthy(written_ns=NOW_NS - 600 * NS)
    assert names(Observation(True, False, silent, NOW_NS)) == ["engine-silent"]
    stale = healthy(feed={"seconds_since_last_frame": 900.0})
    assert names(Observation(True, False, stale, NOW_NS)) == ["feed"]
    closed = healthy(feed={"seconds_since_last_frame": 900.0}, trading_active=False)
    assert names(Observation(True, False, closed, NOW_NS)) == []  # quiet market, not a fault
    broken = healthy(ledger={"verified": False, "reconciled_exact": False})
    assert names(Observation(True, False, broken, NOW_NS)) == ["ledger-chain", "ledger-reconcile"]
    failing = healthy(backup={"configured": True, "hours_since": 1, "last_error": "ShareError"})
    late = healthy(backup={"configured": True, "hours_since": 40, "last_error": None})
    unset = healthy(backup={"configured": False, "hours_since": None, "last_error": None})
    assert names(Observation(True, False, failing, NOW_NS)) == ["backup"]
    assert names(Observation(True, False, late, NOW_NS)) == ["backup"]
    assert names(Observation(True, False, unset, NOW_NS)) == []
    assert names(Observation(True, False, healthy(storage_state="warn"), NOW_NS)) == ["storage"]


def test_confirm_remind_and_clear() -> None:
    rules, state = Rules(), State()
    problem = [Problem("feed", "stale")]
    assert plan(state, problem, 0, rules) == []  # one check: not yet
    first = plan(state, problem, 1, rules)
    assert [m.kind for m in first] == ["problem"]
    committed(state, first[0], 1)
    assert plan(state, problem, 2, rules) == []  # told: quiet until the reminder
    later = 1 + 6 * 3_600 * NS
    reminder = plan(state, problem, later, rules)
    assert [m.kind for m in reminder] == ["reminder"]
    committed(state, reminder[0], later)
    cleared = plan(state, [], later + 1, rules)
    assert [(m.kind, m.text) for m in cleared] == [("recovered", "recovered: stale")]
    committed(state, cleared[0], later + 1)
    assert state.problems == {}
    blip = State()
    plan(blip, problem, 0, rules)
    assert plan(blip, [], 1, rules) == [] and blip.problems == {}  # never told: forgotten


def test_a_damaged_watchdog_state_starts_fresh(tmp_path: Path) -> None:
    (tmp_path / "w.json").write_text("{}", encoding="utf-8")
    assert load_state(tmp_path / "w.json") == State()


def test_a_dead_engine_is_restarted_and_said_at_once(tmp_path: Path) -> None:
    toasts, started = FakeNotifier(), list[list[str]]()
    desk = AlertDesk(toasts, tmp_path, QUIET)
    did = check(tmp_path, desk, NOON, start=started.append, command=["run", "it"])
    assert did == {"running": False, "restarted": True, "problems": 1, "shown": 1}
    assert started == [["run", "it"]]
    said = "the engine had stopped; the watchdog restarted it"
    assert toasts.shown == [("IKARS: engine restarted", said, False)]


def test_the_restart_limit_then_one_urgent_alert_each(tmp_path: Path) -> None:
    toasts, started = FakeNotifier(), list[list[str]]()
    desk = AlertDesk(toasts, tmp_path, QUIET)
    for n in range(5):
        check(tmp_path, desk, NOON + timedelta(minutes=5 * n), start=started.append, command=["x"])
    assert len(started) == 3  # the limit in an hour
    urgent = sorted(text for _, text, loud in toasts.shown if loud)
    assert urgent == [  # each said once, after its two-check confirmation
        "the engine is not running",
        "the engine was restarted 3 times in an hour; not again",
    ]
    later = NOON + timedelta(minutes=70)  # the hour is up: tried again
    check(tmp_path, desk, later, start=started.append, command=["x"])
    assert len(started) == 4


def test_an_engine_the_owner_stopped_is_left_alone(tmp_path: Path) -> None:
    runmode.request_stop(tmp_path)
    started: list[list[str]] = []
    did = check(tmp_path, AlertDesk(FakeNotifier(), tmp_path, QUIET), NOON, start=started.append)
    assert did["restarted"] is False and started == []


def test_a_running_engine_is_watched_through_its_snapshot(tmp_path: Path) -> None:
    lock = runmode.EngineLock(tmp_path)
    lock.acquire()
    toasts = FakeNotifier()
    desk = AlertDesk(toasts, tmp_path, QUIET)
    broken = {"verified": False, "reconciled_exact": True}
    for minutes in (0, 5):  # the engine writes a fresh snapshot before each check
        moment = NOON + timedelta(minutes=minutes)
        written = int(moment.timestamp()) * NS - 30 * NS
        runmode.write_snapshot(tmp_path, healthy(written_ns=written, ledger=broken))
        check(tmp_path, desk, moment, start=lambda command: None)
    text = "the ledger's hash chain is broken: nothing is appended"
    assert toasts.shown[-1] == ("IKARS: problem", text, True)
    moment = NOON + timedelta(minutes=10)
    runmode.write_snapshot(tmp_path, healthy(written_ns=int(moment.timestamp()) * NS))
    check(tmp_path, desk, moment, start=lambda command: None)
    assert toasts.shown[-1][0] == "IKARS: recovered"
    lock.release()


def test_a_notification_that_fails_is_tried_again(tmp_path: Path) -> None:
    no_restarts = Rules(restart_limit=0)
    desk = AlertDesk(FakeNotifier(fail=True), tmp_path, QUIET)
    for n in range(3):
        moment = NOON + timedelta(minutes=5 * n)
        check(tmp_path, desk, moment, start=lambda command: None, rules=no_restarts)
    state = load_state(tmp_path / "watchdog.json")
    assert state.problems["engine"].alerted_ns is None  # never shown, so never counted as told
    working = AlertDesk(FakeNotifier(), tmp_path, QUIET)
    moment = NOON + timedelta(minutes=15)
    check(tmp_path, working, moment, start=lambda command: None, rules=no_restarts)
    assert load_state(tmp_path / "watchdog.json").problems["engine"].alerted_ns is not None


def test_the_engine_command_is_windowless_and_rooted(tmp_path: Path) -> None:
    command = engine_command(tmp_path)
    assert command[1:4] == ["-m", "ikars_engine", "serve"]
    assert command[-2:] == ["--root", str(tmp_path)]
    assert engine_command(None)[-1] == "serve"


# --- auto-start ---------------------------------------------------------------------------------


class FakeRegistry:
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


def test_install_and_remove_are_user_space_and_symmetric() -> None:
    registry, commands = FakeRegistry(), []

    def run(argv: list[str]) -> int:
        commands.append(argv)
        return 0

    python = r"C:\Program Files\Python\pythonw.exe"
    assert autostart.install(registry, run, python) == {
        "autostart": True, "notification_name": True, "watchdog_task": True,
    }  # fmt: skip
    engine = registry.get(autostart.RUN_KEY, autostart.RUN_VALUE)
    assert engine == f'"{python}" -m ikars_engine serve'
    assert registry.get(autostart.APP_KEY, "DisplayName") == "IKARS"
    create = commands[0]
    assert create[:3] == ["schtasks", "/Create", "/F"] and "/RU" not in create  # the user's own
    assert create[create.index("/MO") + 1] == "5"
    assert autostart.installed(registry, run) == {
        "autostart": True, "notification_name": True, "watchdog_task": True,
    }  # fmt: skip
    assert autostart.remove(registry, run)["autostart"] is True
    assert registry.values == {}
    assert autostart.remove(registry, lambda argv: 1) == {
        "autostart": False, "notification_name": False, "watchdog_task": False,
    }  # fmt: skip


# --- the commands -------------------------------------------------------------------------------


def test_a_second_serve_is_refused_and_stop_marks_the_owner(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ikars_engine.__main__ import main

    data = tmp_path / "data"
    lock = runmode.EngineLock(data)
    lock.acquire()
    assert main(["python -m ikars_engine", "serve", "--root", str(tmp_path)]) == 1
    assert json.loads(capsys.readouterr().out) == {"refused": "the engine is already running"}
    lock.release()
    assert main(["python -m ikars_engine", "stop", "--root", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"stopped": True, "was_running": False}
    assert runmode.stopped_by_owner(data) and not runmode.stop_requested(data)


@pytest.mark.skipif(not hasattr(__import__("sys"), "getwindowsversion"), reason="Windows only")
def test_the_user_registry_round_trip() -> None:
    """The real HKCU calls, on a throwaway key that the test removes again."""
    import uuid

    registry = autostart.UserRegistry()
    parent = r"Software\IKARS-tests"
    key = rf"{parent}\{uuid.uuid4().hex}"
    try:
        assert registry.get(key, "Name") is None
        registry.set(key, "Name", "value")
        assert registry.get(key, "Name") == "value"
        assert registry.delete_value(key, "Name") is True
        assert registry.delete_value(key, "Name") is False
    finally:
        assert registry.delete_key(key) is True
        registry.delete_key(parent)
    assert registry.delete_key(key) is False


def test_a_throttled_alert_is_not_counted_as_told(tmp_path: Path) -> None:
    """Per-commit review: a problem back within the throttle was marked told and went quiet."""
    toasts = FakeNotifier()
    desk = AlertDesk(toasts, tmp_path, QUIET)
    no_restarts = Rules(restart_limit=0)
    desk.raise_alert(Alert("watchdog:engine", URGENT, "problem", "earlier"), NOON)  # just shown
    for n in (1, 2):  # confirmed at the second check, but throttled
        check(tmp_path, desk, NOON + timedelta(minutes=n), start=lambda c: None, rules=no_restarts)
    assert load_state(tmp_path / "watchdog.json").problems["engine"].alerted_ns is None
    did = check(
        tmp_path, desk, NOON + timedelta(minutes=11), start=lambda c: None, rules=no_restarts
    )
    assert did["shown"] != 0 and toasts.shown[-1][1] == "the engine is not running"


def test_install_passes_the_data_folder_to_both(tmp_path: Path) -> None:
    """Per-commit review: a custom --root reached neither the engine nor the watchdog."""
    registry, commands = FakeRegistry(), []

    def run(argv: list[str]) -> int:
        commands.append(argv)
        return 0

    autostart.install(registry, run, "pythonw.exe", tmp_path)
    engine = registry.get(autostart.RUN_KEY, autostart.RUN_VALUE)
    assert engine is not None and engine.endswith(f"serve --root {tmp_path}")
    assert commands[0][-1].endswith(f"watchdog --root {tmp_path}")


def test_a_background_engine_that_fails_says_so_in_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Live finding: in the background (no console) a failing start left no trace anywhere."""
    from ikars_engine.__main__ import main

    async def failing(*args: Any, **kwargs: Any) -> dict[str, object]:
        raise RuntimeError("the key variables are not set")

    monkeypatch.setattr("ikars_engine.kalshi.runner.run", failing)
    with caplog.at_level("ERROR"):
        assert main(["python -m ikars_engine", "serve", "--root", str(tmp_path)]) == 1
    logged = json.dumps([r.__dict__ for r in caplog.records], default=str)
    assert "the key variables are not set" in logged
    assert not runmode.engine_running(tmp_path / "data")  # the lock is released
