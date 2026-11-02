"""M1.13: replaying real recordings. The live source runs on made-up frames (the test-data policy),
records them, and the replay of that recording must hand the engine the very same items and keep
the same state; then a signal plugin runs on a replay, deterministically."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import msgspec
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
from ikars_core.book import Applied
from ikars_core.models import Market
from ikars_engine import replay_cli
from ikars_engine.clocks import VirtualClock, exact_ns, ns_time
from ikars_engine.kalshi.discovery import MarketIndex, market_row
from ikars_engine.kalshi.rows import KalshiState, delta_message, snapshot_message
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import PluginBase, PluginContext, PluginKind, PluginSpec
from ikars_engine.recorder import SCHEMAS, Recorder, compact, hour_of, read_hour, write_items
from ikars_engine.replay import (
    RecordingReplay,
    book_agreement,
    compare,
    merged_rows,
    recommendations,
    run_replay,
)
from kalshi_fakes import (
    DELTA_MSG,
    RIGS,
    SNAPSHOT_MSG,
    TICKER_MSG,
    TRADE_MSG,
    Rig,
    frame,
    market,
)

NS = 1_000_000_000
T0 = 1_767_225_600 * NS  # 2026-01-01 00:00 UTC, as in the Kalshi tests
HOUR = 3_600 * NS


@pytest.fixture(autouse=True)
def close_rigs() -> Iterator[None]:
    yield
    while RIGS:
        RIGS.pop().state.close()


def same(a: Item, b: Item) -> bool:
    return (a.ref, a.source, a.received_ns, a.published_ns, a.payload, a.hops) == (
        b.ref,
        b.source,
        b.received_ns,
        b.published_ns,
        b.payload,
        b.hops,
    )


async def replayed(root: Path, start: int, end: int) -> tuple[list[Item], RecordingReplay]:
    clock = VirtualClock(start)
    source = RecordingReplay(root, start, end, clock)
    return [i async for i in source.items()], source


# --- one row, one item ---------------------------------------------------------------------------


async def test_a_replay_hands_the_engine_the_live_sources_very_items(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1"), market("M2")])
    await rig.source.start()
    book = rig.feed.book_sid()
    lifecycle = {"market_ticker": "M1", "event_type": "close_date_updated", "close_ts": 9}
    combo = {**TICKER_MSG, "market_ticker": "KXMVE-COMBO-1"}
    for event in (
        frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + 10, seq=1, sid=book),
        frame("orderbook_delta", DELTA_MSG, T0 + 20, seq=2, sid=book),
        frame("ticker", TICKER_MSG, T0 + 30, sid=1),
        frame("ticker", combo, T0 + 31, sid=1),  # every market's ticker is recorded now
        frame("trade", TRADE_MSG, T0 + 40, seq=1, sid=2),
        frame("market_lifecycle_v2", lifecycle, T0 + 50, seq=1, sid=3),
        frame("orderbook_snapshot", {"market_ticker": "M2"}, T0 + 60, seq=3, sid=book),
    ):
        await rig.feed.queue.put(event)
    live = await rig.items(7)
    await rig.source.stop()  # closes and compacts the recording
    back, source = await replayed(tmp_path / "rec", T0, T0 + HOUR)
    assert len(back) == len(live) == 7
    assert all(same(a, b) for a, b in zip(live, back, strict=True))  # identical by construction
    assert live[3].ref == Ref("ticker", "KXMVE-COMBO-1")
    # the state, rebuilt the same way: an empty snapshot is a book with nothing, not no book
    assert source.state.books["M1"].best_bid("yes") == rig.source.books["M1"].best_bid("yes")
    assert source.state.books["M2"].ready and not source.state.books["M2"].yes
    assert source.state.quotes == rig.source.quotes == {"M1": (490_000, 510_000)}  # no combo
    assert back[5].payload["event_type"] == "close_date_updated"
    assert json.loads(back[5].payload["message"])["close_ts"] == 9  # the message as received


async def test_feed_events_keep_the_replayed_books_as_live(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    book = rig.feed.book_sid()
    await rig.feed.queue.put(frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + 10, seq=1, sid=book))
    await rig.items(1)
    from ikars_core.ws import SequenceGap

    rig.clock.advance_to(T0 + 20)
    rig.source._sequence_gap(SequenceGap(sid=book, expected=2, got=5, generation=1))
    assert rig.source.books["M1"].broken  # a gap never reads as a sound book (M1.13)
    rig.source._checkpoint(T0 + HOUR)  # a broken book is never checkpointed
    await rig.source.stop()
    _, source = await replayed(tmp_path / "rec", T0, T0 + 2 * HOUR)
    assert source.state.books["M1"].broken
    rows = list(merged_rows(tmp_path / "rec", T0, T0 + 2 * HOUR, ("feed",)))
    events = [(r["event"], r["detail"], r["received_ns"] - T0) for _, r in rows]
    # the start before the first frame; the gap just after the last frame the pump handled
    assert [e[:2] for e in events] == [
        ("start", "subscribing"),
        ("watch", "M1"),
        ("sequence_gap", "expected 2, got 5"),
        ("watch", "M1"),  # the hourly checkpoint restates the watched set
    ]
    assert events[0][2] == 0 and events[2][2] == 11 and events[3][2] == HOUR


def test_the_state_follows_snapshots_deltas_and_feed_events() -> None:
    state = KalshiState()
    rows = [
        {
            "received_ns": 1,
            "ticker": "M",
            "snapshot": 1,
            "clock": None,
            "checkpoint": False,
            "side": side,
            "price_e6": p,
            "count_e2": c,
        }
        for side, p, c in (("yes", 400_000, 100), ("no", 500_000, 200))
    ]
    state.snapshot(snapshot_message(rows), 1)
    delta = {
        "ticker": "M",
        "clock": None,
        "side": "yes",
        "price_e6": 400_000,
        "delta_e2": -300,
        "ts_ms": 1,
    }
    assert state.delta(delta_message(delta), 2) is Applied.BROKEN  # a level would go negative
    assert state.delta(delta_message({**delta, "ticker": "N"}), 3) is Applied.NO_SNAPSHOT
    state.snapshot(snapshot_message(rows), 4)
    assert state.books["M"].ready
    state.feed_event({"received_ns": 5, "event": "unwatch", "channel": "book", "detail": "M"})
    assert "M" not in state.books
    state.snapshot(snapshot_message(rows), 6)
    state.ticker({"received_ns": 7, "ticker": "M", "price_e6": 1, "yes_bid_e6": 2, "yes_ask_e6": 3})
    state.feed_event({"received_ns": 8, "event": "forget", "channel": "", "detail": "M"})
    assert "M" not in state.books and "M" not in state.quotes
    state.snapshot(snapshot_message(rows), 9)
    state.feed_event({"received_ns": 10, "event": "reconnect", "channel": "book", "detail": ""})
    assert state.books == {}


# --- the reader ----------------------------------------------------------------------------------


def test_channels_merge_in_received_order_ties_by_channel(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path)
    recorder.write("trade", {"received_ns": T0 + 5, "ticker": "M", "trade_id": "a"})
    recorder.write("ticker", {"received_ns": T0 + 5, "ticker": "M"})
    recorder.write("feed", {"received_ns": T0 + 5, "event": "start"})
    recorder.write("trade", {"received_ns": T0 + HOUR + 1, "ticker": "M", "trade_id": "b"})
    recorder.write("ticker", {"received_ns": T0 + 1, "ticker": "M"})
    recorder.close()
    order = [(c, r["received_ns"] - T0) for c, r in merged_rows(tmp_path, T0, T0 + 2 * HOUR)]
    assert order == [("ticker", 1), ("feed", 5), ("ticker", 5), ("trade", 5), ("trade", HOUR + 1)]
    window = [r["received_ns"] - T0 for _, r in merged_rows(tmp_path, T0 + 2, T0 + 6)]
    assert window == [5, 5, 5]  # only the window's rows


def test_an_open_hour_and_parts_are_read_and_sorted(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, flush_seconds=0)
    recorder.write("trade", {"received_ns": T0 + 9, "ticker": "M", "trade_id": "late"})
    recorder.write("trade", {"received_ns": T0 + 3, "ticker": "M", "trade_id": "early"})
    recorder.flush()  # still open: an .arrow file, out of order
    ids = read_hour(tmp_path, "trade", hour_of(T0)).column("trade_id").to_pylist()
    assert ids == ["early", "late"]
    recorder.close()


def test_a_recording_from_before_a_column_existed_still_compacts_and_reads(tmp_path: Path) -> None:
    old = pa.schema([f for f in SCHEMAS["book_delta"] if f.name != "clock"])  # before M1.13
    folder = tmp_path / "book_delta" / hour_of(T0)[0]
    folder.mkdir(parents=True)
    path = folder / f"{hour_of(T0)[1]}.arrow"
    table = pa.table(
        {
            "received_ns": [T0],
            "ts_ms": [1],
            "ticker": ["M"],
            "seq": [1],
            "side": ["yes"],
            "price_e6": [1],
            "delta_e2": [1],
        },
        schema=old,
    )
    with pa.OSFile(str(path), "wb") as sink:
        writer = ipc.new_stream(sink, old)
        writer.write_table(table)
        writer.close()
    compact(path, "book_delta")
    [row] = read_hour(tmp_path, "book_delta", hour_of(T0)).to_pylist()
    assert row["clock"] is None and row["seq"] == 1


async def test_late_rows_are_released_at_once_never_back_in_time(tmp_path: Path) -> None:
    from ikars_engine.recorder import item_row

    def made(n: str, at: int) -> Item:
        return Item(ref=Ref("x", n), source="s", received_ns=T0 + at)

    recorder = Recorder(tmp_path)
    recorder.write("item", item_row(made("a", 50)))
    recorder.close()
    behind = Recorder(tmp_path)  # a pump far behind: its row's hour is closed, so it lands later
    behind._closed_through["item"] = T0
    behind.write("item", item_row(made("b", 5)))
    behind.write("item", item_row(made("c", HOUR + 10)))
    behind.close()
    back, source = await replayed(tmp_path, T0, T0 + 2 * HOUR)
    assert [i.ref.id for i in back] == ["a", "b", "c"] and source.stats.late_rows == 1
    assert back[1].received_ns == T0 + 5  # the item keeps its time; the clock never goes back


# --- market details ------------------------------------------------------------------------------


def test_the_index_records_a_market_when_its_details_change_not_its_volume() -> None:
    rows: list[dict[str, Any]] = []
    index = MarketIndex(now_ns=lambda: T0)
    index.record = rows.append
    index.fee_terms["KXDEMO"] = ("quadratic", 70_000)
    index.put(Market.model_validate(market("A")))
    index.put(Market.model_validate(market("A", volume="99.00")))  # volume only: not recorded
    index.put(Market.model_validate(market("A", status="closed")))
    assert [r["status"] for r in rows] == ["active", "closed"]
    assert rows[0]["fee_type"] == "quadratic" and rows[0]["fee_multiplier_e6"] == 70_000
    assert index.dump() == 1 and rows[-1]["full"] is True
    info = index.markets["A"]
    row = market_row(info, None, T0)
    assert row["close_ns"] == (None if info.close_time is None else exact_ns(info.close_time))
    if info.close_time is not None:
        assert ns_time(row["close_ns"]) == info.close_time  # exact both ways


# --- a signal on a replay ------------------------------------------------------------------------


class Mover(PluginBase):
    """A made-up signal: one recommendation the first time a market's YES bid moves."""

    spec = PluginSpec(
        name="mover",
        kind=PluginKind.SIGNAL,
        version="1",
        consumes=("ticker",),
        produces=("recommendation",),
    )

    def __init__(self) -> None:
        self.bids: dict[str, int | None] = {}

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        from ikars_engine.journal import Recommendation

        ticker, bid = item.ref.id, item.payload.get("yes_bid_e6")
        info = ctx.services["market"](ticker)
        before = self.bids.get(ticker)
        self.bids[ticker] = bid
        if (
            bid is None
            or info is None
            or before is None
            or before == bid
            or ctx.services["active"]("mover", ticker, item.received_ns)
        ):
            return
        rec = Recommendation(
            ticker=ticker,
            event_ticker=info.event_ticker,
            side="yes",
            entry_price_e6=bid + 10_000,
            count_e2=100,
            expires_ns=item.received_ns + 1_800 * NS,
            summary="moved",
            strategy="mover",
            version="1",
            horizons_s=[60],
            inputs=[item.ref],
            pnl_e6=0,
        )
        await ctx.emit(
            Item(
                ref=Ref("recommendation", f"mover-{item.received_ns}"),
                source="mover",
                received_ns=item.received_ns,
                payload=msgspec.to_builtins(rec),
            )
        )


def made_up_window(root: Path) -> None:
    recorder = Recorder(root)
    info_row = {
        "received_ns": T0 - 2 * HOUR,
        "ticker": "M",
        "event_ticker": "E",
        "series_ticker": "S",
        "category": "c",
        "title": "t",
        "status": "active",
        "close_ns": T0 + 9 * HOUR,
        "result": None,
        "climate_code": None,
        "fee_type": "quadratic",
        "fee_multiplier_e6": 70_000,
        "full": True,
    }
    recorder.write("market", info_row)
    for n, bid in enumerate([400_000, 400_000, 420_000, 430_000]):
        recorder.write(
            "ticker",
            {
                "received_ns": T0 + HOUR + n * NS,
                "ts_ms": n,
                "ticker": "M",
                "price_e6": bid,
                "yes_bid_e6": bid,
                "yes_ask_e6": bid + 20_000,
            },
        )
    # a recommendation the live run recorded: left out, the replay makes its own
    recorder.write(
        "item",
        {
            "received_ns": T0 + HOUR,
            "published_ns": None,
            "kind": "recommendation",
            "id": "old",
            "source": "mover",
            "hops": 1,
            "payload": "{}",
        },
    )
    recorder.close()


async def test_a_signal_on_a_replay_is_deterministic_and_reported_from_its_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made_up_window(tmp_path / "rec")
    monkeypatch.setattr("ikars_engine.plugins.discover", lambda: ([Mover()], []))
    window = (T0 + HOUR, T0 + 2 * HOUR)
    first = await run_replay(tmp_path / "rec", tmp_path / "a", window, warmup_ns=0)
    monkeypatch.setattr("ikars_engine.plugins.discover", lambda: ([Mover()], []))
    second = await run_replay(tmp_path / "rec", tmp_path / "b", window, warmup_ns=0)
    assert first["digest"] == second["digest"] and first["recommendations"] == 1
    assert first["market_rows"] == 1 and first["partial"] == []
    assert first["items"] == {"ticker": 4}  # the recorded recommendation was left out
    made = recommendations(tmp_path / "a" / "journal.sqlite", *window)
    assert made == recommendations(tmp_path / "b" / "journal.sqlite", *window)
    assert made[0][1:3] == ("M", "yes") and made[0][3] == T0 + HOUR + 2 * NS
    assert compare(made, made) == {
        "live": 1,
        "replayed": 1,
        "matched": 1,
        "live_only": 0,
        "replay_only": 0,
    }
    nothing = await run_replay(
        tmp_path / "rec", tmp_path / "c", (T0 + HOUR + 3 * NS, T0 + 2 * HOUR), warmup_ns=0
    )
    assert nothing["recommendations"] == 0  # made before this window: not reported


async def test_only_signal_plugins_are_replayed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a signal plugin here: nws"):
        await run_replay(tmp_path, tmp_path / "out", (T0, T0 + HOUR), plugins=["nws"])


def test_a_window_without_market_details_says_it_is_partial(tmp_path: Path) -> None:
    write_items(tmp_path, [Item(ref=Ref("x", "1"), source="s", received_ns=T0 + 1)])
    report = asyncio.run(run_replay(tmp_path, tmp_path / "out", (T0, T0 + HOUR), plugins=[]))
    assert report["partial"] and report["items"] == {"x": 1}


def test_the_book_check_reads_empty_sides_and_counts_settled_books(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path)
    for side, price in (("yes", 400_000), ("no", 500_000)):
        recorder.write(
            "book_snapshot",
            {
                "received_ns": T0 + 1,
                "ticker": "M",
                "snapshot": 1,
                "side": side,
                "price_e6": price,
                "count_e2": 100,
                "clock": None,
                "checkpoint": False,
            },
        )
    recorder.write(
        "ticker",
        {"received_ns": T0 + 2, "ticker": "M", "yes_bid_e6": 400_000, "yes_ask_e6": 500_000},
    )  # in flight: the book changed 1 ns ago
    recorder.write(
        "ticker",
        {"received_ns": T0 + NS, "ticker": "M", "yes_bid_e6": 400_000, "yes_ask_e6": 500_000},
    )  # settled, and equal
    recorder.write(
        "book_snapshot",
        {
            "received_ns": T0 + 2 * NS,
            "ticker": "N",
            "snapshot": 2,
            "side": "yes",
            "price_e6": 300_000,
            "count_e2": 100,
            "clock": None,
            "checkpoint": False,
        },
    )
    recorder.write(
        "ticker",
        {"received_ns": T0 + 3 * NS, "ticker": "N", "yes_bid_e6": 300_000, "yes_ask_e6": 1_000_000},
    )  # $1: no ask, as the empty NO side says
    recorder.close()
    found = book_agreement(tmp_path, T0, T0 + HOUR)
    assert found == {"compared": 3, "agreed": 3, "settled": 2, "settled_agreed": 2}


def test_the_command_reads_local_time_and_refuses_a_backward_window(tmp_path: Path) -> None:
    assert replay_cli.moment("2026-01-01T00:00:00+00:00") == T0
    from ikars_engine.paths import Paths

    args = type("A", (), {"start": "2026-01-01T01:00+00:00", "end": "2026-01-01T00:00+00:00"})()
    assert replay_cli.run(args, Paths(tmp_path), {}) == 2


async def test_a_checkpoint_sets_a_book_and_a_backfilled_trade_is_no_item(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    book = rig.feed.book_sid()
    await rig.feed.queue.put(frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + 10, seq=1, sid=book))
    await rig.items(1)
    rig.clock.advance_to(T0 + HOUR + 5)
    rig.source._checkpoint(T0 + HOUR + 5)  # the next hour's copy of every sound book
    rig.source._checkpoint(T0 + HOUR + 6)  # once an hour only
    rig.recorder.write(
        "trade",
        {
            "received_ns": T0 + HOUR + 7,
            "ts_ms": 1,
            "ticker": "M1",
            "trade_id": "b1",
            "backfilled": True,
        },
    )
    await rig.source.stop()
    back, source = await replayed(tmp_path / "rec", T0 + HOUR, T0 + 2 * HOUR)
    assert back == []  # a checkpoint and a backfilled trade: neither was ever an item live
    assert source.state.books["M1"].best_bid("yes") == rig.source.books["M1"].best_bid("yes")
    assert source.stats.rows == {"feed": 1, "book_snapshot": 1, "trade": 1}  # watch, checkpoint


def test_the_command_replays_compares_and_checks_books(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import argparse

    from ikars_engine.paths import Paths

    made_up_window(tmp_path / "ikars" / "data" / "recordings")
    monkeypatch.setattr("ikars_engine.plugins.discover", lambda: ([Mover()], []))
    args = argparse.Namespace(
        start="2026-01-01T01:00+00:00",
        end="2026-01-01T02:00+00:00",
        plugins="mover",
        warmup_min=0,
        out=tmp_path / "out",
        compare=True,
        check_books=True,
    )
    assert replay_cli.run(args, Paths(tmp_path / "ikars"), {}) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["recommendations"] == 1 and report["gaps"] == []
    assert (
        report["compare"]["live"] == 0 and report["compare"]["replay_only"] == 1
    )  # no live journal
    assert report["books"]["compared"] == 0 and report["books"]["share"] is None
    wrong = argparse.Namespace(**{**vars(args), "plugins": "nothing"})
    assert replay_cli.run(wrong, Paths(tmp_path / "ikars"), {}) == 2
    inside = argparse.Namespace(**{**vars(args), "out": tmp_path / "ikars" / "data"})
    assert replay_cli.run(inside, Paths(tmp_path / "ikars"), {}) == 2  # never the live journal
    again = argparse.Namespace(**{**vars(args), "compare": False, "check_books": False})
    assert replay_cli.run(again, Paths(tmp_path / "ikars"), {}) == 2  # an earlier run's journal


def test_an_hour_compacted_while_it_is_read_is_never_read_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ikars_engine import recorder as rec

    first = Recorder(tmp_path)
    first.write("trade", {"received_ns": T0 + 1, "ticker": "M", "trade_id": "a"})
    first.close()  # the hour's Parquet
    second = Recorder(tmp_path, flush_seconds=0)
    second.write("trade", {"received_ns": T0 + 2, "ticker": "M", "trade_id": "b"})
    second.flush()  # and an open stream beside it
    real = rec._read_stream
    calls: list[Path] = []

    def compacting(path: Path, schema: pa.Schema) -> pa.Table:
        rows = real(path, schema)
        if not calls:  # the live engine folds the stream in while the replay reads it
            calls.append(path)
            second.close()
        return rows

    monkeypatch.setattr(rec, "_read_stream", compacting)
    ids = read_hour(tmp_path, "trade", hour_of(T0)).column("trade_id").to_pylist()
    assert ids == ["a", "b"] and calls  # read again once the files stopped changing


def test_a_watched_combo_keeps_its_quote_and_a_checkpoint_is_no_change() -> None:
    clock = VirtualClock(T0)
    replay = RecordingReplay(Path("nowhere"), T0, T0 + HOUR, clock)
    level = {
        "ticker": "KXMVE-C",
        "snapshot": 1,
        "clock": None,
        "side": "yes",
        "price_e6": 400_000,
        "count_e2": 100,
    }
    replay._apply(
        "feed", {"received_ns": T0, "event": "watch", "channel": "book", "detail": "KXMVE-C"}
    )
    replay._apply(
        "book_snapshot",
        {"received_ns": T0, "rows": [{**level, "received_ns": T0, "checkpoint": False}]},
    )
    replay._apply(
        "ticker",
        {
            "received_ns": T0 + 1,
            "ticker": "KXMVE-C",
            "ts_ms": 1,
            "price_e6": 1,
            "yes_bid_e6": 2,
            "yes_ask_e6": 3,
        },
    )
    assert replay.state.quotes["KXMVE-C"] == (2, 3)  # watched: kept, as live
    changed = replay.state.book_changed_ns["KXMVE-C"]
    replay._apply(
        "book_snapshot",
        {"received_ns": T0 + 5, "rows": [{**level, "received_ns": T0 + 5, "checkpoint": True}]},
    )
    assert replay.state.book_changed_ns["KXMVE-C"] == changed  # a checkpoint changes nothing


async def test_a_full_cycle_records_changes_as_they_come(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("A"), market("B")])
    rows: list[dict[str, Any]] = []
    index = rig.discovery.index
    index.record = rows.append
    index.categories["KXDEMO"] = "Crypto"  # as the cycle's series list says
    index.fee_terms["KXDEMO"] = ("quadratic", 500_000)
    index.put(Market.model_validate(market("A")))
    index.put(Market.model_validate(market("C")))  # known before, unchanged by the cycle
    rows.clear()
    rig.kalshi.markets = [market("A", status="closed"), market("B"), market("C")]
    await rig.discovery.full()
    # A's change and B's arrival are recorded as they come; the dump writes only C
    assert [(r["ticker"], r["status"], r["full"]) for r in rows] == [
        ("A", "closed", False), ("B", "active", False), ("C", "active", True)
    ]  # fmt: skip


def test_a_market_the_index_drops_is_dropped_in_a_replay_too() -> None:
    from ikars_engine.replay import ReplayMarkets

    rows: list[dict[str, Any]] = []
    index = MarketIndex(now_ns=lambda: T0)
    index.record = rows.append
    index.put(Market.model_validate(market("S", status="settled")))
    info = index.markets["S"]
    assert index.prune(T0 + 10**18) == ["S"]  # long after it closed
    assert rows[-1]["removed"] is True and rows[-1]["ticker"] == "S"
    markets = ReplayMarkets()
    for row in rows:
        markets.apply(row)
    assert "S" not in markets.markets and markets.rows == 2
    assert market_row(info, None, T0)["removed"] is False


async def test_a_checkpoint_lands_between_the_frames_the_pump_really_handled(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [market("M1")])
    await rig.source.start()
    book = rig.feed.book_sid()
    await rig.feed.queue.put(
        frame("orderbook_snapshot", SNAPSHOT_MSG, T0 + HOUR - 5, seq=1, sid=book)
    )
    await rig.items(1)
    rig.clock.advance_to(T0 + HOUR + 30)
    rig.source._pump_at = T0 + HOUR + 10  # the pump has handled frames up to here...
    rig.source._checkpoint(T0 + HOUR + 30)  # ...when the heartbeat copies the books
    await rig.source.stop()
    rows = list(merged_rows(tmp_path / "rec", T0 + HOUR, T0 + 2 * HOUR, ("book_snapshot",)))
    stamps = {r["received_ns"] - T0 - HOUR for _, r in rows if r["checkpoint"]}
    assert stamps and all(10 < s < 30 for s in stamps)  # after what was handled, not "now"
