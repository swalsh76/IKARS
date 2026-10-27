"""The state store and its migrations, the recorder and its compaction, and the storage governor."""

import os
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from ikars_engine.governor import Storage, StorageGovernor
from ikars_engine.recorder import Recorder, compact, hours, read_rows
from ikars_engine.store import STATE_MIGRATIONS, StateStore, StoreError, open_store

HOUR_NS = 3_600 * 10**9
T0 = 1_767_225_600 * 10**9  # 2026-01-01 00:00 UTC


# --- the state store ------------------------------------------------------------------------------


def test_a_new_store_is_created_at_the_latest_schema(tmp_path: Path) -> None:
    db = open_store(tmp_path / "s.sqlite", STATE_MIGRATIONS)
    assert db.execute("PRAGMA user_version").fetchone()[0] == len(STATE_MIGRATIONS)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    db.close()
    assert not list(tmp_path.glob("*.bak.sqlite"))  # nothing to back up when new


def test_an_upgrade_backs_the_file_up_first(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    open_store(path, ["CREATE TABLE a (x INTEGER)"]).close()
    db = open_store(path, ["CREATE TABLE a (x INTEGER)", "CREATE TABLE b (y INTEGER)"])
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()
    backups = list(tmp_path.glob("s.*.bak.sqlite"))
    assert len(backups) == 1
    old = sqlite3.connect(backups[0])
    assert old.execute("PRAGMA user_version").fetchone()[0] == 1
    old.close()


def test_a_newer_store_is_refused_and_a_failed_step_leaves_the_old_version(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    open_store(path, ["CREATE TABLE a (x INTEGER)", "CREATE TABLE b (y INTEGER)"]).close()
    with pytest.raises(StoreError, match="newer IKARS"):
        open_store(path, ["CREATE TABLE a (x INTEGER)"])
    with pytest.raises(sqlite3.OperationalError):
        open_store(path, ["CREATE TABLE a (x INTEGER)", "CREATE TABLE b (y INTEGER)", "NOT SQL"])
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    db.close()


def test_gaps_bookmarks_heartbeats_and_the_exchange(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.sqlite")
    trade = state.add_gap("trade", "reconnect", 10, 20, "backfill", 21, "down 1 s")
    ticker = state.add_gap("ticker", "reconnect", 10, 20, "unrecoverable", 21)
    assert [g.id for g in state.gaps("open")] == [trade]
    assert state.gaps("unrecoverable")[0].id == ticker
    state.finish_gap(trade, "done", "12 trades")
    assert state.gaps("open") == [] and state.gaps("done")[0].detail == "12 trades"
    with pytest.raises(StoreError):
        state.finish_gap(trade, "open", "")
    assert state.bookmark("b") is None
    state.set_bookmark("b", 5)
    state.set_bookmark("b", 7)
    assert state.bookmark("b") == 7
    state.beat({"trade": 1, "ticker": 2})
    assert state.heartbeats() == {"trade": 1, "ticker": 2} and not state.shut_down_cleanly()
    state.mark_clean_shutdown(3)
    assert state.shut_down_cleanly()
    state.beat({"trade": 4})  # running again: no longer a clean shutdown
    assert not state.shut_down_cleanly()
    assert state.last_exchange_status() is None
    state.add_exchange_status(1, True, False, "{}")
    assert state.last_exchange_status() == (True, False, "{}")
    state.close()


# --- the recorder -------------------------------------------------------------------------------


def trade(at: int, trade_id: str, backfilled: bool = False) -> dict[str, object]:
    return {"received_ns": at, "ts_ms": at // 10**6, "ticker": "T", "trade_id": trade_id,
            "yes_price_e6": 400_000, "no_price_e6": 600_000, "count_e2": 100,
            "taker_side": "yes", "backfilled": backfilled}  # fmt: skip


def test_rows_are_compacted_per_hour_sorted_and_trades_kept_once(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, flush_seconds=3600)
    recorder.write("trade", trade(T0 + 2, "b"))
    recorder.write("trade", trade(T0 + 1, "a"))
    recorder.write("trade", trade(T0 + 3, "a", backfilled=True))  # a backfill repeats "a"
    recorder.write("trade", trade(T0 + HOUR_NS + 1, "c"))  # the next hour
    recorder.close()
    files = hours(tmp_path, "trade")
    assert [f"{p.parent.name}/{p.name}" for p in files] == [
        "2026-01-01/00.parquet",
        "2026-01-01/01.parquet",
    ]
    first = read_rows(files[:1], "trade")
    assert first.column("trade_id").to_pylist() == ["a", "b"]
    assert first.column("backfilled").to_pylist() == [False, False]  # the first copy is kept
    assert not list(tmp_path.glob("*/*/*.arrow"))
    assert recorder.rows_written["trade"] == 4


def test_a_crash_leaves_an_arrow_file_that_the_next_start_compacts(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, flush_seconds=0)
    for n in range(3):
        recorder.write("trade", trade(T0 + n, str(n)))
    arrow = next(tmp_path.glob("trade/*/*.arrow"))
    # a hard crash: the writer is never closed (the dead process releases the file), and the last
    # batch is torn
    next(iter(recorder._open.values()))[1].close()
    data = arrow.read_bytes()
    arrow.write_bytes(data[: len(data) - 20])
    restarted = Recorder(tmp_path)
    assert restarted.recover() == 1
    rows = read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist()
    assert rows == ["0", "1"]  # the torn last batch is lost, the rest kept


def test_a_restart_in_the_same_hour_merges_with_what_was_written(tmp_path: Path) -> None:
    first = Recorder(tmp_path)
    first.write("trade", trade(T0 + 1, "a"))
    first.close()
    second = Recorder(tmp_path)
    second.write("trade", trade(T0 + 2, "b"))
    second.close()
    assert read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist() == ["a", "b"]


def test_an_unknown_channel_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no such channel"):
        Recorder(tmp_path).write("orders", {"received_ns": 1})


def test_compaction_keeps_the_schema(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, flush_seconds=0)
    recorder.write(
        "book_delta",
        {
            "received_ns": T0,
            "ts_ms": 1,
            "ticker": "T",
            "seq": 5,
            "side": "yes",
            "price_e6": 410_000,
            "delta_e2": -500,
        },
    )
    path = next(tmp_path.glob("book_delta/*/*.arrow"))
    recorder._open.clear()  # leave the writer behind, as a crash would
    target = compact(path, "book_delta")
    assert pq.read_table(target).column("delta_e2").to_pylist() == [-500]


# --- the storage governor -----------------------------------------------------------------------


def make_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_the_governor_warns_then_prunes_the_oldest_compacted_hours(tmp_path: Path) -> None:
    old = make_file(tmp_path / "trade/2026-01-01/00.parquet", 400)
    mid = make_file(tmp_path / "book_delta/2026-01-01/01.parquet", 300)
    new = make_file(tmp_path / "trade/2026-01-02/00.parquet", 200)
    open_hour = make_file(tmp_path / "trade/2026-01-02/01.arrow", 50)
    governor = StorageGovernor(tmp_path, budget_bytes=1_000)
    assert governor.check().state is Storage.PRUNED  # 950 of 1000: over 95%
    assert not old.exists() and mid.exists() and new.exists() and open_hour.exists()
    # 550 left of a 560 budget: over 95%, so the oldest (mid) goes; the open hour never does
    assert StorageGovernor(tmp_path, budget_bytes=560).check().state is Storage.PRUNED
    assert not mid.exists() and new.exists() and open_hour.exists()
    assert StorageGovernor(tmp_path, budget_bytes=10_000).check().state is Storage.OK


def test_the_governor_warning_level(tmp_path: Path) -> None:
    make_file(tmp_path / "trade/2026-01-01/00.parquet", 850)
    report = StorageGovernor(tmp_path, budget_bytes=1_000).check()
    assert report.state is Storage.WARN and report.pruned == [] and report.used_percent == 85.0
    with pytest.raises(ValueError):
        StorageGovernor(tmp_path, warn_percent=99, prune_percent=95)
    assert os.path.exists(tmp_path / "trade/2026-01-01/00.parquet")


def test_the_first_row_of_a_new_hour_is_never_lost(tmp_path: Path) -> None:
    """Found by the first run of these tests: the hour's flush took the pending list away, and the
    new hour's first row was appended to the orphaned list and lost."""
    recorder = Recorder(tmp_path, flush_seconds=3600)
    recorder.write("trade", trade(T0 + 1, "old-hour"))
    recorder.write("trade", trade(T0 + HOUR_NS + 1, "new-hour"))
    recorder.close()
    rows = read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist()
    assert rows == ["old-hour", "new-hour"]


def test_a_compaction_cut_off_between_its_steps_is_finished_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 10: a crash between writing the Parquet and deleting the IPC file duplicated rows."""
    first = Recorder(tmp_path)
    first.write("trade", trade(T0 + 1, "a"))
    first.close()
    second = Recorder(tmp_path)
    second.write("trade", trade(T0 + 2, "b"))

    def crash(merged: Path) -> Path:
        raise OSError("the power went")

    monkeypatch.setattr("ikars_engine.recorder.finish_compaction", crash)
    second.close()  # the merged rows are in the temporary file; the IPC file is renamed
    assert second.compaction_failures == 1
    assert list(tmp_path.glob("trade/*/*.arrow.merged")) and list(tmp_path.glob("trade/*/*.tmp"))
    monkeypatch.undo()
    assert Recorder(tmp_path).recover() == 1
    assert read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist() == ["a", "b"]
    assert not list(tmp_path.glob("trade/*/*.arrow*")) and not list(tmp_path.glob("trade/*/*.tmp"))


def test_a_compaction_that_fails_leaves_the_hour_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2: a file held by another program used to stop the recording."""
    recorder = Recorder(tmp_path)
    recorder.write("trade", trade(T0 + 1, "a"))

    def held(path: Path, channel: str) -> Path:
        raise PermissionError("in use")

    monkeypatch.setattr("ikars_engine.recorder.compact", held)
    recorder.close()
    assert recorder.compaction_failures == 1 and list(tmp_path.glob("trade/*/*.arrow"))
    monkeypatch.undo()
    assert Recorder(tmp_path).recover() == 1
    assert read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist() == ["a"]


def test_a_damaged_hour_never_stops_a_start_and_is_kept_aside(tmp_path: Path) -> None:
    """M1.R 3: a truncated Parquet hour (a power cut) made every start fail."""
    first = Recorder(tmp_path)
    first.write("trade", trade(T0 + 1, "a"))
    first.close()
    target = next(tmp_path.glob("trade/*/*.parquet"))
    target.write_bytes(b"PAR1 not really")  # damaged
    crashed = Recorder(tmp_path, flush_seconds=0)
    crashed.write("trade", trade(T0 + 2, "b"))
    next(iter(crashed._open.values()))[1].close()  # a crash leaves the hour open
    assert Recorder(tmp_path).recover() == 1  # no exception: the start goes on
    assert read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist() == ["b"]
    assert [p.name for p in target.parent.glob("*.damaged")] == ["00.parquet.damaged"]


def test_an_hour_that_cannot_be_folded_in_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1.R 5: after a failed fold-in, the hour's earlier file was reopened and emptied."""
    first = Recorder(tmp_path, flush_seconds=0)
    first.write("trade", trade(T0 + 1, "a"))
    next(iter(first._open.values()))[1].close()  # left open by a crash

    def held(path: Path, channel: str) -> Path:
        raise PermissionError("in use")

    monkeypatch.setattr("ikars_engine.recorder.compact", held)
    second = Recorder(tmp_path, flush_seconds=0)
    second.write("trade", trade(T0 + 2, "b"))
    folder = next(tmp_path.glob("trade/*"))
    assert sorted(p.name for p in folder.glob("*.arrow")) == ["00.1.arrow", "00.arrow"]
    assert second.known_trade_ids(T0, T0 + 10) == {"a", "b"}  # both parts are read
    for _, sink, _ in second._open.values():
        sink.close()
    second._open.clear()
    monkeypatch.undo()
    assert Recorder(tmp_path).recover() == 2
    ids = read_rows(hours(tmp_path, "trade"), "trade").column("trade_id").to_pylist()
    assert sorted(ids) == ["a", "b"]


def test_an_hour_closes_only_after_its_grace_period(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, flush_seconds=3600)
    recorder.write("trade", trade(T0 + 1, "a"))
    assert recorder.close_finished(T0 + HOUR_NS + 119 * 10**9) == 0
    assert recorder.close_finished(T0 + HOUR_NS + 120 * 10**9) == 1
    assert hours(tmp_path, "trade") == [] and recorder.compact_pending() == 1
    assert len(hours(tmp_path, "trade")) == 1


def test_a_row_later_than_the_grace_never_reopens_a_closed_hour(tmp_path: Path) -> None:
    """Per-commit review: a closed hour may be compacting in a worker; reopening it raced that."""
    recorder = Recorder(tmp_path, flush_seconds=3600)
    recorder.write("trade", trade(T0 + 1, "a"))
    assert recorder.close_finished(T0 + HOUR_NS + 120 * 10**9) == 1
    recorder.write("trade", trade(T0 + 2, "late"))  # received in hour 00, written after it closed
    recorder.flush()
    assert recorder.late_rows == 1
    assert [hour for _, hour in recorder._open] == [("2026-01-01", "01")]  # 00 not reopened
    recorder.close()
    files = hours(tmp_path, "trade")
    assert [p.name for p in files] == ["00.parquet", "01.parquet"]
    assert read_rows(files[1:], "trade").column("trade_id").to_pylist() == ["late"]
