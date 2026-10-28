"""M1.12a: plugins in the live engine, what they emit recorded, and the recommendation journal."""

import asyncio
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pyarrow.parquet as pq
import pytest
from ikars_engine import upgrades
from ikars_engine.clocks import VirtualClock
from ikars_engine.journal import (
    ACTIVE,
    EXPIRED,
    SUPERSEDED,
    Journal,
    JournalService,
    Recommendation,
)
from ikars_engine.kalshi.runner import recording
from ikars_engine.model import Item, Ref
from ikars_engine.paths import Paths
from ikars_engine.plugins import PluginContext, PluginKind, PluginSpec
from ikars_engine.recorder import Recorder

S = 1_000_000_000
T0 = 1_767_225_600 * S  # 2026-01-01 00:00 UTC


def rec(ticker: str = "KXDEMO-26JAN01-T50", **extra: Any) -> dict[str, Any]:
    made = Recommendation(
        ticker=ticker, side="yes", entry_price_e6=420_000, count_e2=1_000,
        expires_ns=T0 + 1800 * S, summary="made up: up 5c in 5 min", strategy="momentum",
        version="9f2ce1a0", horizons_s=[1800, 3600], inputs=[Ref("market", ticker)],
    )  # fmt: skip
    return {**msgspec.to_builtins(made), **extra}


def item(rid: str, at: int, payload: dict[str, Any]) -> Item:
    return Item(ref=Ref("recommendation", rid), source="momentum", received_ns=at, payload=payload,
                hops=1)  # fmt: skip


def test_a_recommendation_is_kept_superseded_by_a_newer_one_and_expires(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    journal.add(item("r1", T0, rec()))
    journal.add(item("r2", T0 + 60 * S, rec()))  # the same rule, the same market: newer
    journal.add(item("r3", T0 + 90 * S, rec("KXDEMO-26JAN01-T60")))
    first, *_ = [e for e in journal.recent() if e.id == "r1"]
    assert first.state == SUPERSEDED and first.superseded_by == "r2"
    assert [e.id for e in journal.recent()] == ["r3", "r2", "r1"]  # newest first
    assert journal.expire(T0 + 1800 * S) == 2  # r2 and r3: active and past their expiry
    assert {e.id: e.state for e in journal.recent()} == {"r1": SUPERSEDED, "r2": EXPIRED,
                                                        "r3": EXPIRED}  # fmt: skip
    assert journal.add(item("r1", T0, rec())) is False  # the same id again: nothing changes
    assert len(journal.recent()) == 3
    journal.close()


def test_marks_are_written_once_at_each_horizon(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    journal.add(item("r1", T0, rec()))
    assert journal.due(T0 + 1799 * S) == []
    assert journal.due(T0 + 1800 * S) == [("r1", "KXDEMO-26JAN01-T50", 1800, T0 + 1800 * S)]
    journal.mark("r1", 1800, T0 + 1800 * S, 440_000, 450_000)
    assert journal.due(T0 + 3600 * S) == [("r1", "KXDEMO-26JAN01-T50", 3600, T0 + 3600 * S)]
    journal.mark("r1", 3600, T0 + 3600 * S, None, 460_000)  # no bid at that moment
    journal.mark("r1", 3600, T0 + 3700 * S, 1, 1)  # never twice
    [entry] = journal.recent()
    assert [(m.horizon_s, m.yes_bid_e6, m.yes_ask_e6) for m in entry.marks] == [
        (1800, 440_000, 450_000), (3600, None, 460_000),
    ]  # fmt: skip
    assert entry.recommendation.inputs == [Ref("market", "KXDEMO-26JAN01-T50")]
    journal.close()


def test_a_payload_that_is_not_a_recommendation_is_refused(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    with pytest.raises(msgspec.ValidationError):
        journal.add(item("bad", T0, rec(order="buy now")))  # unknown fields: refused
    with pytest.raises(msgspec.ValidationError):
        journal.add(item("bad", T0, {"ticker": "X"}))
    assert journal.recent() == []
    journal.close()


async def test_the_service_queues_writes_expires_and_marks_from_the_live_quotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(T0)
    quotes = {"KXDEMO-26JAN01-T50": (440_000, 450_000)}
    service = JournalService(Journal(tmp_path / "journal.sqlite"), quotes, clock.now_ns)
    service.listen(item("r1", T0, rec()))
    service.listen(item("bad", T0, {"ticker": "X"}))
    service.listen(Item(ref=Ref("signal", "s"), source="x", received_ns=T0))  # not for it
    assert service.queue.qsize() == 2
    clock.advance_to(T0 + 1800 * S)
    service._write([service.queue.get_nowait(), service.queue.get_nowait()])
    assert service.health() == {"recorded": 1, "rejected": 1, "dropped": 0, "failures": 0}
    [entry] = service.journal.recent()
    assert entry.state == EXPIRED and entry.marks[0].yes_bid_e6 == 440_000
    full = JournalService(service.journal, quotes, clock.now_ns, queue=asyncio.Queue(1))
    full.listen(item("r2", T0, rec()))
    full.listen(item("r3", T0, rec()))  # never blocks a plugin: counted
    assert full.dropped == 1
    service.journal.close()


def recorded(folder: Path) -> list[dict[str, Any]]:
    [hour] = list(folder.glob("item/*/*.parquet"))
    rows: list[dict[str, Any]] = pq.read_table(hour).to_pylist()
    return rows


async def test_the_host_stamps_received_time_and_the_runner_records_what_plugins_emit(
    tmp_path: Path,
) -> None:
    clock = VirtualClock(T0 + 42 * S)
    emitted: list[Item] = []

    async def emit(made: Item) -> None:
        emitted.append(made)

    spec = PluginSpec(name="momentum", kind=PluginKind.SIGNAL, version="0.1",
                      consumes=("market",), produces=("recommendation",))  # fmt: skip
    ctx = PluginContext(spec, clock, {}, emit, 1.0)
    await ctx.emit(item("r1", 7, rec()))  # the plugin's own time is ignored
    assert emitted[0].received_ns == T0 + 42 * S and emitted[0].hops == 1
    recorder = Recorder(tmp_path, flush_seconds=3600)
    recording(recorder)(emitted[0])
    recorder.close()
    rows = recorded(tmp_path)
    assert rows[0]["kind"] == "recommendation" and rows[0]["received_ns"] == T0 + 42 * S
    assert msgspec.json.decode(rows[0]["payload"])["strategy"] == "momentum"


async def test_the_api_lists_the_journal_s_recommendations(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from ikars_engine import contracts as c
    from ikars_engine.api import ApiContext, create_app
    from ikars_engine.kalshi.discovery import MarketIndex
    from ikars_engine.ledger import Ledger
    from ikars_engine.metrics import Metrics
    from ikars_engine.portfolio import Held, portfolio

    journal = Journal(tmp_path / "journal.sqlite")
    journal.add(item("r1", T0, rec()))
    journal.mark("r1", 1800, T0 + 1800 * S, 440_000, 450_000)
    ledger = Ledger(tmp_path / "ledger.sqlite", ZoneInfo("America/Denver"))
    ctx = ApiContext(
        token="t" * 43, health=dict, index=MarketIndex(), quotes={}, last_price={}, changed_ns={},
        book_changed_ns={}, books={}, book_version={}, watched=set, ledger=ledger,
        ledger_health=dict,
        portfolio=lambda: portfolio({}, lambda s: None, lambda s: None, Held({}, 0)),
        alert_log=tmp_path / "alerts.jsonl", metrics=Metrics(), now_ns=lambda: T0, port=47821,
        journal=journal,
    )  # fmt: skip
    auth = {"Authorization": f"Bearer {'t' * 43}"}
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        page = msgspec.json.decode(
            (await http.get("/v1/recommendations", headers=auth)).content, type=c.RecommendationPage
        )
    [view] = page.items
    assert view.id == "r1" and view.state == ACTIVE and view.version == "9f2ce1a0"
    assert view.at_utc.startswith("2026-01-01T00:00:00") and view.inputs == [
        "market:KXDEMO-26JAN01-T50"
    ]
    assert view.marks[0].horizon_s == 1800 and view.marks[0].yes_ask_e6 == 450_000
    journal.close()
    ledger.close()


def test_the_journal_is_in_the_pre_upgrade_snapshot(tmp_path: Path) -> None:
    paths = Paths(tmp_path / "IKARS").create()
    paths.settings.write_text("version = 5\n", encoding="utf-8")
    Journal(paths.data / "journal.sqlite").close()
    upgrades.before_start(paths, "0.1.0")
    made = upgrades.before_start(paths, "0.2.0")
    assert made is not None and (made / "journal.sqlite").is_file()


def test_the_same_id_again_never_supersedes_the_newer_one(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    journal.add(item("r1", T0, rec()))
    journal.add(item("r2", T0 + 60 * S, rec()))
    assert journal.add(item("r1", T0 + 120 * S, rec())) is False  # a restart re-emits r1
    assert {e.id: e.state for e in journal.recent()} == {"r2": ACTIVE, "r1": SUPERSEDED}
    journal.close()


@pytest.mark.parametrize(
    "bad",
    [
        {"entry_price_e6": 0}, {"entry_price_e6": 2_000_000}, {"count_e2": 10**12},
        {"pnl_e6": 2**60}, {"horizons_s": [0]}, {"horizons_s": [40 * 86_400]},
        {"expires_ns": T0 - 1}, {"expires_ns": T0 + 31 * 86_400 * S},
    ],
)  # fmt: skip
def test_values_out_of_range_are_refused(tmp_path: Path, bad: dict[str, Any]) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    with pytest.raises((msgspec.ValidationError, ValueError)):
        journal.add(item("bad", T0, rec(**bad)))
    journal.close()


def test_a_long_horizon_is_marked_and_a_missing_quote_waits_for_one(tmp_path: Path) -> None:
    clock = VirtualClock(T0)
    quotes: dict[str, tuple[int | None, int | None]] = {}
    service = JournalService(Journal(tmp_path / "journal.sqlite"), quotes, clock.now_ns)
    two_days = 2 * 86_400
    service._write(
        [item("r1", T0, rec(horizons_s=[1800, two_days], expires_ns=T0 + 3 * 86_400 * S))]
    )
    clock.advance_to(T0 + 1800 * S)
    service._write([])
    assert service.journal.recent()[0].marks == []  # no quote yet: it waits
    clock.advance_to(T0 + 1800 * S + 601 * S)  # after ten minutes: marked without one
    service._write([])
    assert [(m.horizon_s, m.yes_bid_e6) for m in service.journal.recent()[0].marks] == [
        (1800, None)
    ]
    quotes["KXDEMO-26JAN01-T50"] = (300_000, 310_000)
    clock.advance_to(T0 + two_days * S)  # a two-day horizon: still due, never dropped
    service._write([])
    assert service.journal.recent()[0].marks[-1].horizon_s == two_days
    service.journal.close()


async def test_what_is_queued_at_shutdown_is_written(tmp_path: Path) -> None:
    clock = VirtualClock(T0)
    service = JournalService(Journal(tmp_path / "journal.sqlite"), {}, clock.now_ns)
    service.listen(item("r1", T0, rec()))
    service.flush()
    assert [e.id for e in service.journal.recent()] == ["r1"]
    service.journal.close()


def test_the_journal_learns_settlements_once_a_minute(tmp_path: Path) -> None:
    clock = VirtualClock(T0)
    results: dict[str, str | None] = {"KXDEMO-26JAN01-T50": None}
    service = JournalService(Journal(tmp_path / "journal.sqlite"), {}, clock.now_ns,
                             result=lambda t: results.get(t), trading=lambda t: False)  # fmt: skip
    service._write([item("r1", T0, rec())])
    assert service.journal.unsettled(T0) == ["KXDEMO-26JAN01-T50"]
    results["KXDEMO-26JAN01-T50"] = "yes"
    clock.advance_to(T0 + 30 * S)
    service._write([])  # within the minute: not looked up again yet
    assert service.journal.recent()[0].settled is None
    clock.advance_to(T0 + 61 * S)
    service._write([])
    assert service.journal.recent()[0].settled == "yes" and service.journal.unsettled(T0) == []
    clock.advance_to(T0 + 1800 * S + 601 * S)  # its check, no quote: marked as closed
    service._write([])
    assert service.journal.recent()[0].marks[0].closed is True
    service.journal.close()


def test_an_expired_recommendation_is_never_superseded(tmp_path: Path) -> None:
    journal = Journal(tmp_path / "journal.sqlite")
    journal.add(item("r1", T0, rec()))  # valid until T0 + 30 min
    journal.add(item("r2", T0 + 1800 * S, rec(expires_ns=T0 + 3600 * S)))  # the second it ends
    assert {e.id: e.state for e in journal.recent()} == {"r2": ACTIVE, "r1": ACTIVE}
    journal.expire(T0 + 1800 * S)
    assert {e.id: e.state for e in journal.recent()}["r1"] == EXPIRED  # the expirer's, not r2's
    journal.close()


def test_a_journal_from_v0_1_7_gains_settlements_with_a_backup(tmp_path: Path) -> None:
    from ikars_engine.journal import JOURNAL_MIGRATIONS
    from ikars_engine.store import open_store

    path = tmp_path / "journal.sqlite"
    open_store(path, JOURNAL_MIGRATIONS[:1]).close()  # the schema v0.1.7 shipped
    journal = Journal(path)
    journal.settle("KXDEMO-26JAN01-T50", "no", T0)
    journal.close()
    assert list(tmp_path.glob("journal.*.bak.sqlite"))  # the old file kept first
