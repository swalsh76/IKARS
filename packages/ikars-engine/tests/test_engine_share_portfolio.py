"""M1.5b: backups to the share (the guard, the tree, retention, the restore drill), the backup
service, and the portfolio (FIFO lots, marked at the bid after the exit fee). The "share" here is a
folder of the test's own; every account record is made up."""

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from ikars_core.fees import FeeSchedule
from ikars_engine.backup_service import LAST_OK, BackupService
from ikars_engine.clocks import VirtualClock
from ikars_engine.kalshi.runner import PortfolioHealth
from ikars_engine.ledger import Entry, Ledger
from ikars_engine.ledger_import import fill_cash, replay
from ikars_engine.paths import Paths
from ikars_engine.portfolio import holdings, portfolio
from ikars_engine.share import (
    MARKER,
    SUMS,
    Share,
    ShareError,
    back_up,
    migration_copier,
    restore,
    restore_drill,
    sha256,
)
from ikars_engine.store import StateStore, open_store

T0 = 1_767_225_600 * 10**9  # 2026-01-01 00:00 UTC
DAY_NS = 86_400 * 10**9
UTC = ZoneInfo("UTC")


# --- the share and its guard ----------------------------------------------------------------------


@pytest.fixture
def nas(tmp_path: Path) -> Path:
    """A share as KAPS left it: its marker and its backup repository, which IKARS must not touch."""
    share = tmp_path / "nas"
    (share / "pgbackrest").mkdir(parents=True)
    (share / ".kaps-backup-marker").write_text("kaps", encoding="utf-8")
    (share / "pgbackrest" / "archive.bin").write_bytes(b"kaps data")
    return share


def kaps_untouched(share: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (str(p.relative_to(share)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in share.rglob("*")
        if p.is_file() and not p.is_relative_to(share / "ikars")
    )


def test_the_guard_keeps_every_path_inside_the_subtree(nas: Path) -> None:
    share = Share(str(nas))
    assert share.path("ledger", "x") == nas / "ikars" / "ledger" / "x"
    for escape in (("..", ".kaps-backup-marker"), ("ledger", "..", "..", "pgbackrest")):
        with pytest.raises(ShareError, match="leave"):
            share.path(*escape)
    with pytest.raises(ShareError, match="no share"):
        Share("  ")


def test_init_creates_the_folder_and_marker_only(nas: Path, tmp_path: Path) -> None:
    before = kaps_untouched(nas)
    share = Share(str(nas))
    assert share.problem() is not None and "marker" in share.problem()  # type: ignore[operator]
    assert share.init(T0) is True and share.init(T0) is False
    assert sorted(p.name for p in (nas / "ikars").iterdir()) == [MARKER]
    assert share.problem() is None and kaps_untouched(nas) == before
    with pytest.raises(ShareError, match="not reachable"):
        Share(str(tmp_path / "unmounted")).init(T0)
    assert Share(str(tmp_path / "unmounted")).problem() == "the share is not reachable"


# --- backups ------------------------------------------------------------------------------------


def make_data(root: Path, records: int = 3) -> Paths:
    paths = Paths(root)
    paths.create()
    ledger = Ledger(paths.ledger / "ledger.sqlite", UTC)
    for n in range(records):
        ledger.append(Entry("deposit", T0 + n, "deposit", f"d{n}", cash_e6=1_000_000))
    ledger.checkpoint(paths.ledger / "checkpoints", T0)
    ledger.close()
    StateStore(paths.data / "state.sqlite").close()
    paths.settings.write_text("version = 3\n", encoding="utf-8")
    return paths


def run_backup(share: Share, paths: Paths, now_ns: int = T0) -> Any:
    return back_up(
        share,
        ledger=paths.ledger / "ledger.sqlite",
        state=paths.data / "state.sqlite",
        settings=paths.settings,
        checkpoints=paths.ledger / "checkpoints",
        staging=paths.data / "backup-staging",
        now_ns=now_ns,
    )


def test_a_backup_writes_the_tree_and_nothing_else(nas: Path, tmp_path: Path) -> None:
    before = kaps_untouched(nas)
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    report = run_backup(share, paths)
    assert (report.day, report.ledger_head.seq, report.checkpoints, report.pruned) == (
        "2026-01-01", 3, 1, 0,
    )  # fmt: skip
    root = nas / "ikars"
    daily = root / "ledger" / "daily" / "2026" / "2026-01-01"
    assert sorted(p.name for p in daily.iterdir()) == [
        SUMS,
        "checkpoint.json",
        "ikars-ledger.sqlite",
    ]
    assert share.check_folder(daily) and share.check_folder(
        root / "ledger" / "monthly" / "2026" / "2026-01"
    )
    assert (root / "ledger" / "checkpoints" / "2026" / "2026-01-01.json").is_file()
    assert share.check_folder(root / "state" / "daily" / "2026" / "2026-01-01")
    assert (root / "config" / "settings.toml").read_text(encoding="utf-8") == "version = 3\n"
    assert not list(root.rglob("*.tmp"))
    assert not any((paths.data / "backup-staging").iterdir())  # each run removes its own
    assert kaps_untouched(nas) == before
    again = run_backup(share, paths)  # the same day again: replaced, nothing new sent
    assert again.checkpoints == 1 and share.check_folder(daily)  # the newest is always resent


def test_without_the_marker_nothing_is_written(nas: Path, tmp_path: Path) -> None:
    paths = make_data(tmp_path / "ikars-data")
    with pytest.raises(ShareError, match="marker"):
        run_backup(Share(str(nas)), paths)
    assert not (nas / "ikars").exists()


def test_a_copy_that_does_not_match_is_refused(
    nas: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share = Share(str(nas))
    share.init(T0)
    source = tmp_path / "file.bin"
    source.write_bytes(b"the real bytes")

    def corrupting(src: Path, dst: Path) -> None:
        Path(dst).write_bytes(b"not the bytes")

    monkeypatch.setattr("ikars_engine.share.shutil.copyfile", corrupting)
    with pytest.raises(ShareError, match="does not match"):
        share.put(source, "config", "file.bin")
    assert not list((nas / "ikars").rglob("file.bin*"))


def test_old_dailies_are_pruned_and_the_rest_kept(nas: Path, tmp_path: Path) -> None:
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    run_backup(share, paths)  # 2026-01-01
    later = T0 + 95 * DAY_NS  # 2026-04-06: the ledger's January daily is past 90 days
    report = run_backup(share, paths, later)
    root = nas / "ikars"
    assert report.pruned == 2  # the ledger's and the state's January dailies
    assert not (root / "ledger" / "daily" / "2026" / "2026-01-01").exists()
    assert (root / "ledger" / "monthly" / "2026" / "2026-01").exists()  # monthlies are kept
    assert (root / "ledger" / "daily" / "2026" / "2026-04-06").exists()
    (root / "ledger" / "daily" / "2026" / "not-a-date").mkdir()
    assert run_backup(share, paths, later).pruned == 0  # odd names are never removed


# --- the restore drill --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error", [sqlite3.DatabaseError("file is not a database"), KeyError("seq")]
)
def test_a_restored_copy_that_is_not_a_sound_ledger_fails_the_drill(
    nas: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """M1.R 22: a damaged copy that matched its checksums crashed the drill with a traceback."""
    share = Share(str(nas))
    share.init(T0)

    def damaged(*_: object) -> None:
        raise error

    monkeypatch.setattr("ikars_engine.share._drill", damaged)
    local = Ledger(tmp_path / "local.sqlite", UTC)
    drill = restore_drill(share, local, tmp_path / "drill")
    local.close()
    assert not drill.ok and drill.problem is not None and "not a sound ledger" in drill.problem


def test_the_restore_drill_proves_the_newest_backup(nas: Path, tmp_path: Path) -> None:
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    local = Ledger(paths.ledger / "ledger.sqlite", UTC)
    no_backup = restore_drill(share, local, tmp_path / "drill0")
    assert not no_backup.ok and no_backup.problem == "no ledger backup on the share"
    run_backup(share, paths)
    drill = restore_drill(share, local, tmp_path / "drill1")
    assert (drill.ok, drill.day, drill.records) == (True, "2026-01-01", 3)
    local.close()
    other = Ledger(tmp_path / "other.sqlite", UTC)  # a different ledger: the head is not in it
    other.append(Entry("deposit", T0, "deposit", "zz", cash_e6=5))
    assert restore_drill(share, other, tmp_path / "drill2").problem == (
        "the copy's head is not in the local ledger"
    )
    other.close()
    copy = nas / "ikars" / "ledger" / "daily" / "2026" / "2026-01-01" / "ikars-ledger.sqlite"
    copy.write_bytes(copy.read_bytes() + b"tampered")
    local = Ledger(paths.ledger / "ledger.sqlite", UTC)
    assert restore_drill(share, local, tmp_path / "drill3").problem == (
        "a backup file does not match its SHA256SUMS"
    )
    local.close()


# --- the copy before a migration ------------------------------------------------------------------


def test_a_migration_backup_also_goes_to_the_share(
    nas: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    share = Share(str(nas))
    share.init(T0)
    path = tmp_path / "x.sqlite"
    open_store(path, ["CREATE TABLE a (x INTEGER)"]).close()
    open_store(path, ["CREATE TABLE a (x INTEGER)", "CREATE TABLE b (y INTEGER)"],
               migration_copier(share)).close()  # fmt: skip
    sent = list((nas / "ikars" / "ledger" / "pre-migration").iterdir())
    assert len(sent) == 1 and sent[0].name.startswith("x.") and sent[0].name.endswith(".bak.sqlite")
    assert migration_copier(None) is None
    unreachable = migration_copier(Share(str(tmp_path / "gone")))
    assert unreachable is not None
    with caplog.at_level(logging.WARNING):
        unreachable(sent[0])  # logged, never raised: the local copy still stands
    assert "pre-migration copy not sent" in caplog.text


# --- the backup service -------------------------------------------------------------------------


async def test_the_service_backs_up_when_due_and_reports_failures(
    nas: Path, tmp_path: Path
) -> None:
    paths = make_data(tmp_path / "ikars-data")
    state = StateStore(paths.data / "state.sqlite")
    clock = VirtualClock(T0)
    service = BackupService(Share(str(nas)), paths, state, clock)
    assert await service.run_if_due() is True  # never backed up: due, but the share has no marker
    assert (service.failures, service.last_error) == (1, "ShareError")
    Share(str(nas)).init(T0)
    assert await service.run_if_due() is True and service.last_error is None
    assert state.bookmark(LAST_OK) == T0
    clock.advance_to(T0 + DAY_NS // 2)
    assert await service.run_if_due() is False  # backed up half a day ago
    health = service.health()
    assert health["configured"] and health["hours_since"] == 12 and health["failures"] == 1
    off = BackupService(None, paths, state, clock)
    await off.start()
    await off.run_now()
    await off.stop()
    assert off.health()["configured"] is False and off.runs == 0
    state.close()


# --- the portfolio ------------------------------------------------------------------------------


def fill_entry(
    fill_id: str, ticker: str, side: str, count_e2: int, yes_e6: int, fee_e6: int, before: int
) -> Entry:
    from ikars_core.account import Fill

    no_e6 = 1_000_000 - yes_e6
    model = Fill.model_validate({
        "fill_id": fill_id, "order_id": "o", "ticker": ticker, "outcome_side": side,
        "count_fp": f"{count_e2 / 100:.2f}", "yes_price_dollars": f"{yes_e6 / 1e6:.4f}",
        "no_price_dollars": f"{no_e6 / 1e6:.4f}", "fee_cost": f"{fee_e6 / 1e6:.4f}",
        "is_taker": True, "created_time": "2026-01-01T00:00:00Z",
    })  # fmt: skip
    cash, closed, opened = fill_cash(model, before)
    series, event = ticker.split("-")[0], ticker.rsplit("-", 1)[0]
    return Entry(
        "fill", T0, "fill", fill_id, ticker=ticker, cash_e6=cash,
        fields={"outcome_side": side, "count_e2": count_e2, "closed_e2": closed,
                "opened_e2": opened, "yes_price_e6": yes_e6, "no_price_e6": no_e6,
                "fee_e6": fee_e6, "delta_e2": count_e2 if side == "yes" else -count_e2},
        context={"title": f"Made up {ticker}", "event_ticker": event, "series_ticker": series},
    )  # fmt: skip


def made_up_book(tmp_path: Path) -> Ledger:
    ledger = Ledger(tmp_path / "ledger.sqlite", UTC)
    ledger.append_all([
        Entry("deposit", T0, "deposit", "d", cash_e6=100_000_000),
        # T: 10 YES at 40c (+7c fee), 5 YES at 50c (+3c), then 12 sold at 60c: lot 2 keeps 3
        fill_entry("t1", "KXS-26-T", "yes", 1000, 400_000, 70_000, 0),
        fill_entry("t2", "KXS-26-T", "yes", 500, 500_000, 30_000, 1000),
        fill_entry("t3", "KXS-26-T", "no", 1200, 600_000, 50_000, 1500),
        # U: 1 NO at 30c (+2c); then 2 YES at 75c (+4c): 1 closes the NO, 1 opens YES
        fill_entry("u1", "KXS-26-U", "no", 100, 700_000, 20_000, 0),
        fill_entry("u2", "KXS-26-U", "yes", 200, 750_000, 40_000, -100),
        # W: 2 NO at 40c, no fee
        fill_entry("w1", "KXW-26-W", "no", 200, 600_000, 0, 0),
        # V: bought and settled: gone
        fill_entry("v1", "KXS-26-V", "yes", 100, 500_000, 0, 0),
        Entry("settlement", T0 + 1, "settlement", "v@1", ticker="KXS-26-V", cash_e6=1_000_000),
    ])  # fmt: skip
    return ledger


def portfolio_of(ledger: Ledger, quotes: Any, fees: Any, categories: Any) -> Any:
    return portfolio(quotes, fees, categories, holdings(ledger))


def test_fifo_lots_keep_every_millionth(tmp_path: Path) -> None:
    ledger = made_up_book(tmp_path)
    found = holdings(ledger)
    held, cash = found.lots, found.cash_e6
    assert sorted(held) == ["KXS-26-T", "KXS-26-U", "KXW-26-W"]
    t = held["KXS-26-T"]
    # lot 1 (4.07) closed whole; 2 of lot 2's 5 closed: 2.53 x 3/5 = 1.518 left
    assert (t.side, t.count_e2, t.cost_e6) == ("yes", 300, 1_518_000)
    u = held["KXS-26-U"]  # the open half pays half the fee: 0.75 + 0.02
    assert (u.side, u.count_e2, u.cost_e6) == ("yes", 100, 770_000)
    assert (held["KXW-26-W"].side, held["KXW-26-W"].cost_e6) == ("no", 800_000)
    assert cash == replay(ledger).cash_e6  # the same cash the reconciliation uses
    ledger.close()


def test_positions_are_marked_at_the_bid_after_the_exit_fee(tmp_path: Path) -> None:
    ledger = made_up_book(tmp_path)
    terms = FeeSchedule(1_000_000, 25)
    quotes = {"KXS-26-T": (550_000, 570_000), "KXW-26-W": (580_000, 620_000)}
    held = portfolio_of(ledger, quotes, lambda s: terms, {"KXS": "Sports", "KXW": "Weather"}.get)
    by = {p.ticker: p for p in held.positions}
    t = by["KXS-26-T"]
    # 3 YES at the 55c bid: 1.65; fee 0.07 x 3 x .55 x .45 = 0.051975 -> 0.06; less cost 1.518
    assert (t.exit_price_e6, t.exit_fee_e6, t.unrealised_e6) == (550_000, 60_000, 72_000)
    w = by["KXW-26-W"]
    # 2 NO at the NO bid (1 - .62 = 38c): 0.76; fee 0.07 x 2 x .38 x .62 = 0.032984 -> 0.04
    assert (w.exit_price_e6, w.exit_fee_e6, w.unrealised_e6) == (380_000, 40_000, -80_000)
    assert by["KXS-26-U"].unrealised_e6 is None  # no quote: unpriced, never guessed
    assert held.unrealised_e6 is None  # a partial total would mislead
    assert held.exposure("category") == {"Sports": 1_518_000 + 770_000, "Weather": 800_000}
    assert held.exposure("event_ticker")["KXW-26"] == 800_000
    assert t.title == "Made up KXS-26-T" and held.cost_e6 == 3_088_000
    unmodelled = portfolio_of(ledger, quotes, lambda s: None, lambda s: None)
    assert all(p.unrealised_e6 is None for p in unmodelled.positions)
    no_bid = portfolio_of(ledger, {"KXS-26-T": (0, 10_000)}, lambda s: terms, lambda s: None)
    zero = next(p for p in no_bid.positions if p.ticker == "KXS-26-T")
    assert (zero.exit_fee_e6, zero.unrealised_e6) == (0, -1_518_000)
    health = PortfolioHealth(ledger, quotes, lambda s: terms, lambda s: None)
    assert health() == {"positions": 3, "priced": 2} and health() == health()
    ledger.close()


# --- the commands -------------------------------------------------------------------------------


def test_the_backup_and_drill_commands(
    nas: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ikars_engine.__main__ import main

    root = tmp_path / "ikars-data"
    make_data(root)
    base = ["python -m ikars_engine", "backup", "--root", str(root)]
    assert main([*base, "init", "--share", str(nas)]) == 0
    assert json.loads(capsys.readouterr().out) == {"created": True, "share_saved": True}
    assert str(nas) not in capsys.readouterr().out
    assert main([*base, "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"problem": None, "newest_daily": None}
    assert main([*base, "run"]) == 0
    ran = json.loads(capsys.readouterr().out)
    assert ran["head_seq"] == 3 and ran["pruned"] == 0
    drill = ["python -m ikars_engine", "ledger", "--root", str(root), "restore-drill"]
    assert main(drill) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    os.remove(nas / "ikars" / MARKER)
    assert main([*base, "status"]) == 0
    assert "marker" in json.loads(capsys.readouterr().out)["problem"]


def test_a_snapshot_is_consistent_while_the_ledger_is_open(nas: Path, tmp_path: Path) -> None:
    """The engine keeps the ledger open (WAL) while the backup copies it from a worker thread."""
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    live = Ledger(paths.ledger / "ledger.sqlite", UTC)
    live.append(Entry("deposit", T0 + 9, "deposit", "late", cash_e6=1))  # still in the WAL
    report = run_backup(share, paths)
    assert report.ledger_head.seq == 4
    copy = nas / "ikars" / "ledger" / "daily" / "2026" / "2026-01-01" / "ikars-ledger.sqlite"
    db = sqlite3.connect(copy)
    assert db.execute("SELECT COUNT(*) FROM record").fetchone()[0] == 4
    db.close()
    assert share.check_folder(copy.parent) and len(sha256(copy)) == 64
    live.close()


def test_the_portfolio_command_shapes_dollars_and_counts(tmp_path: Path) -> None:
    from ikars_engine.ledger_cli import _dollars, _summary

    ledger = made_up_book(tmp_path)
    terms = FeeSchedule(1_000_000, 25)
    held = portfolio_of(
        ledger, {"KXS-26-T": (550_000, 570_000)}, lambda s: terms, lambda s: "Sports"
    )
    shown = _dollars(held)
    first = shown["positions"][0]  # type: ignore[index]
    assert first["ticker"] == "KXS-26-T" and first["contracts"] == "3.00"
    assert first["unrealised"] == "0.072000" and shown["unrealised_after_exit_costs"] is None
    assert _summary(held) == {"positions": 3, "priced": 1, "categories": 1}
    ledger.close()


async def test_the_service_loop_starts_and_stops(nas: Path, tmp_path: Path) -> None:
    Share(str(nas)).init(T0)
    paths = make_data(tmp_path / "ikars-data")
    state = StateStore(paths.data / "state.sqlite")
    service = BackupService(Share(str(nas)), paths, state, VirtualClock(T0), check_every_s=3600)
    await service.start()
    for _ in range(500):  # the first pass backs up at once (never backed up)
        if state.bookmark(LAST_OK) is not None:
            break
        await asyncio.sleep(0.01)
    await service.stop()
    assert service.runs == 1 and state.bookmark(LAST_OK) == T0
    state.close()


def test_the_commands_refuse_cleanly_without_a_usable_share(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Per-commit review: no share set, or one that cannot be reached, is a result, not a crash;
    and a share that cannot be reached is never saved."""
    from ikars_engine.__main__ import main
    from ikars_engine.settings import load

    root = tmp_path / "ikars-data"
    make_data(root)
    base = ["python -m ikars_engine", "backup", "--root", str(root)]
    assert main([*base, "status"]) == 1
    assert json.loads(capsys.readouterr().out)["problem"].startswith("no share is set")
    assert main(["python -m ikars_engine", "ledger", "--root", str(root), "restore-drill"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    gone = str(tmp_path / "unmounted")
    assert main([*base, "init", "--share", gone]) == 1
    assert json.loads(capsys.readouterr().out)["problem"] == "the share is not reachable"
    assert load(root / "settings.toml").backup.share == ""  # nothing saved


# --- the M1.5b merge review's findings ----------------------------------------------------------


def test_two_backups_at_once_never_tear_a_copy(nas: Path, tmp_path: Path) -> None:
    """Review 1: the engine's and the command's backups shared a staging folder and temp names."""
    from concurrent.futures import ThreadPoolExecutor

    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    with ThreadPoolExecutor(2) as pool:
        runs = [pool.submit(run_backup, share, paths) for _ in range(2)]
    outcomes = []
    for run in runs:
        try:
            outcomes.append(run.result().ledger_head.seq)
        except (OSError, ShareError):  # at worst one says it failed; nothing is torn
            outcomes.append(None)
    assert 3 in outcomes
    daily = nas / "ikars" / "ledger" / "daily" / "2026" / "2026-01-01"
    assert share.check_folder(daily)
    assert not list((nas / "ikars").rglob("*.tmp"))


def test_a_half_replaced_day_is_ignored_and_swept_later(nas: Path, tmp_path: Path) -> None:
    """Review 2: a day folder is swapped in whole; what a dead run left is swept after an hour."""
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    year = nas / "ikars" / "ledger" / "daily" / "2026"
    year.mkdir(parents=True)
    stale = year / "2026-01-01.dead.old"
    stale.mkdir()
    os.utime(stale, (1, 1))  # long ago
    fresh = year / "2026-01-01.other.new"  # another run's work in progress
    fresh.mkdir()
    assert share.newest_daily() is None  # neither counts as a day
    run_backup(share, paths)
    assert not stale.exists() and fresh.exists()
    assert share.newest_daily() == year / "2026-01-01"


def test_only_missing_and_the_newest_checkpoints_are_sent(nas: Path, tmp_path: Path) -> None:
    """Review 8: every run re-read every checkpoint on the share, a list that only grows."""
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    ledger = Ledger(paths.ledger / "ledger.sqlite", UTC)
    for day in (1, 2):
        ledger.checkpoint(paths.ledger / "checkpoints", T0 + day * DAY_NS)
    ledger.close()
    assert run_backup(share, paths).checkpoints == 3
    assert run_backup(share, paths).checkpoints == 2  # the newest two; the oldest stays


def test_the_drill_says_when_the_share_is_away(
    nas: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 7: an unreachable share read as "no backup", and a read error was a traceback."""
    paths = make_data(tmp_path / "ikars-data")
    local = Ledger(paths.ledger / "ledger.sqlite", UTC)
    away = restore_drill(Share(str(tmp_path / "unmounted")), local, tmp_path / "d1")
    assert away.problem == "the share is not reachable"
    share = Share(str(nas))
    share.init(T0)
    run_backup(share, paths)

    def dropped(*args: Any) -> None:
        raise OSError("the network name is no longer available")

    monkeypatch.setattr("ikars_engine.share.shutil.copyfile", dropped)
    cut = restore_drill(share, local, tmp_path / "d2")
    assert cut.problem == "the share could not be read (OSError)"
    local.close()


def test_portfolio_health_rebuilds_lots_only_when_the_ledger_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 4: every health call replayed the whole ledger on the event loop."""
    ledger = made_up_book(tmp_path)
    calls: list[int] = []

    def counted(led: Ledger) -> Any:
        calls.append(1)
        return holdings(led)

    monkeypatch.setattr("ikars_engine.kalshi.runner.holdings", counted)
    health = PortfolioHealth(ledger, {}, lambda s: None, lambda s: None)
    health()
    health()
    assert len(calls) == 1
    ledger.append(Entry("deposit", T0 + 9, "deposit", "more", cash_e6=1))
    health()
    assert len(calls) == 2
    ledger.close()


def test_a_no_position_without_yes_offers_has_no_bid() -> None:
    """Review 5: a YES ask of 0 (nothing offered) made the NO bid a full dollar."""
    from ikars_engine.portfolio import exit_price

    assert exit_price("no", (None, 0)) == 0
    assert exit_price("no", (None, None)) is None
    assert exit_price("no", (480_000, 520_000)) == 480_000
    assert exit_price("yes", (480_000, 520_000)) == 480_000


async def test_a_daemon_thread_returns_and_raises() -> None:
    """Review 6: a stuck NAS copy must never hold up the engine's exit (a daemon thread)."""
    from ikars_engine.threads import in_daemon_thread

    assert await in_daemon_thread(lambda a, b=0: a + b, 2, b=3) == 5

    def fails() -> None:
        raise ShareError("refused")

    with pytest.raises(ShareError, match="refused"):
        await in_daemon_thread(fails)


def test_the_journal_is_backed_up_with_the_state(nas: Path, tmp_path: Path) -> None:
    from ikars_engine.journal import Journal

    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "ikars-data")
    Journal(paths.data / "journal.sqlite").close()
    back_up(
        share, ledger=paths.ledger / "ledger.sqlite", state=paths.data / "state.sqlite",
        settings=paths.settings, checkpoints=paths.ledger / "checkpoints",
        staging=paths.data / "backup-staging", now_ns=T0, journal=paths.data / "journal.sqlite",
    )  # fmt: skip
    day = nas / "ikars" / "state" / "daily" / "2026" / "2026-01-01"
    assert {p.name for p in day.iterdir()} >= {"state.sqlite", "journal.sqlite", "SHA256SUMS"}


# --- the restore (M1.11: the workstation-lost runbook) --------------------------------------------


def share_files(share: Path) -> list[tuple[str, int, int]]:
    return sorted(
        (str(p.relative_to(share)), p.stat().st_size, p.stat().st_mtime_ns)
        for p in share.rglob("*")
        if p.is_file()
    )


def restore_into(share: Share, paths: Paths) -> Any:
    return restore(
        share, ledger=paths.ledger / "ledger.sqlite", checkpoints=paths.ledger / "checkpoints",
        state=paths.data / "state.sqlite", journal=paths.data / "journal.sqlite",
        settings=paths.settings,
    )  # fmt: skip


def backed_up(nas: Path, tmp_path: Path) -> tuple[Share, Paths]:
    from ikars_engine.journal import Journal

    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "lost")
    Journal(paths.data / "journal.sqlite").close()
    back_up(
        share, ledger=paths.ledger / "ledger.sqlite", state=paths.data / "state.sqlite",
        settings=paths.settings, checkpoints=paths.ledger / "checkpoints",
        staging=paths.data / "backup-staging", now_ns=T0, journal=paths.data / "journal.sqlite",
    )  # fmt: skip
    return share, paths


def test_a_lost_workstation_is_restored_from_the_share_by_the_command(
    nas: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from ikars_engine.__main__ import main

    _, lost = backed_up(nas, tmp_path)
    before = share_files(nas)
    root = tmp_path / "new-machine"
    command = ["ikars", "backup", "--root", str(root), "restore", "--share", str(nas)]
    assert main(command) == 0
    out = capsys.readouterr().out
    assert str(nas) not in out  # the share's path is never printed
    report = json.loads(out)
    assert report["restored"] is True and report["records"] == 3 and report["checkpoints"] == 1
    assert (report["ledger_day"], report["state_day"]) == ("2026-01-01", "2026-01-01")
    assert report["journal"] is True and report["settings"] is True
    new = Paths(root)
    copy = Ledger(new.ledger / "ledger.sqlite", UTC)
    original = Ledger(lost.ledger / "ledger.sqlite", UTC)
    try:
        assert copy.verify().ok and copy.head() == original.head()
    finally:
        copy.close()
        original.close()
    assert len(list((new.ledger / "checkpoints").glob("*/*.json"))) == 1
    assert (new.data / "state.sqlite").is_file() and (new.data / "journal.sqlite").is_file()
    assert new.settings.read_text(encoding="utf-8") == lost.settings.read_text(encoding="utf-8")
    assert not list(new.ledger.glob(".restore-*"))  # the staging folder is gone
    assert share_files(nas) == before  # the share is only read


def test_a_restore_never_overwrites_data(nas: Path, tmp_path: Path) -> None:
    share, lost = backed_up(nas, tmp_path)
    before = sha256(lost.ledger / "ledger.sqlite")
    with pytest.raises(ShareError, match="already holds IKARS data"):
        restore_into(share, lost)
    assert sha256(lost.ledger / "ledger.sqlite") == before
    with pytest.raises(ShareError, match="marker"):
        os.remove(nas / "ikars" / MARKER)
        restore_into(share, Paths(tmp_path / "empty"))


def test_a_bad_backup_leaves_the_new_root_empty(nas: Path, tmp_path: Path) -> None:
    share, _ = backed_up(nas, tmp_path)
    root = Paths(tmp_path / "new-machine")
    saved = nas / "ikars" / "config" / "settings.toml"
    saved.write_text("version = [", encoding="utf-8")  # a file this version cannot read
    with pytest.raises(ShareError, match="saved settings cannot be read"):
        restore_into(share, root)
    assert not [p for p in root.root.rglob("*") if p.is_file()]
    day = nas / "ikars" / "ledger" / "daily" / "2026" / "2026-01-01" / "ikars-ledger.sqlite"
    with open(day, "r+b") as handle:  # a bit flipped on the share
        handle.seek(200)
        byte = handle.read(1)
        handle.seek(200)
        handle.write(bytes([byte[0] ^ 1]))
    with pytest.raises(ShareError, match="SHA256SUMS"):
        restore_into(share, root)
    assert not [p for p in root.root.rglob("*") if p.is_file()]


def test_a_restore_goes_into_empty_folders_and_takes_back_a_part_way_move(
    nas: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    share, _ = backed_up(nas, tmp_path)
    root = Paths(tmp_path / "new-machine")
    root.create()  # the folders a first start makes, with nothing in them (per-commit review)
    (root.ledger / "checkpoints").mkdir(parents=True, exist_ok=True)
    real = Path.replace
    calls = [0]

    def flaky(self: Path, target: Any) -> Path:  # the state file is locked as it moves in
        calls[0] += 1
        if calls[0] == 3:
            raise PermissionError("locked")
        return real(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    with pytest.raises(PermissionError):
        restore_into(share, root)
    assert not [p for p in root.root.rglob("*") if p.is_file()]  # the ledger moved back out
    monkeypatch.setattr(Path, "replace", real)
    assert restore_into(share, root).checkpoints == 1  # and the next try succeeds


def test_a_share_with_only_a_ledger_backup_restores_the_ledger(nas: Path, tmp_path: Path) -> None:
    share = Share(str(nas))
    share.init(T0)
    paths = make_data(tmp_path / "lost")
    folder = nas / "ikars" / "ledger" / "daily" / "2026" / "2026-01-01"
    snapshot_copy = tmp_path / "copy.sqlite"
    copy = Ledger(paths.ledger / "ledger.sqlite", UTC)
    point = copy.checkpoint(tmp_path / "point", T0)
    copy.close()
    from ikars_engine.share import snapshot

    snapshot(paths.ledger / "ledger.sqlite", snapshot_copy)
    share.put_folder({"ikars-ledger.sqlite": snapshot_copy, "checkpoint.json": point},
                     "ledger", "daily", "2026", "2026-01-01")  # fmt: skip
    assert folder.is_dir()
    done = restore_into(share, Paths(tmp_path / "new-machine"))
    assert (done.records, done.checkpoints, done.state_day) == (3, 0, None)
    assert (done.journal, done.settings) == (False, False)
