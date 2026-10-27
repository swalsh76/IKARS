"""The audit ledger (M1.5a): the chain, append-only storage, the account import and its cash rules,
reconciliation, annotations, the service and the tax zone. Every account record here is made up."""

import json
import logging
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from ikars_core.account import (
    Balance,
    Fill,
    InstanceTransfer,
    MarketPosition,
    Positions,
    Settlement,
    Transfer,
)
from ikars_core.endpoints import TypedPage
from ikars_engine import taxzone
from ikars_engine.clocks import VirtualClock
from ikars_engine.ledger import GENESIS, Entry, Ledger, LedgerError, Verified, canonical, digest
from ikars_engine.ledger_cli import status
from ikars_engine.ledger_import import AccountImporter, fill_cash, replay, value_e6
from ikars_engine.ledger_service import LedgerService, annotate
from ikars_engine.settings import migrate

T0 = 1_767_225_600 * 10**9  # 2026-01-01 00:00 UTC
DENVER = ZoneInfo("America/Denver")


def ledger_at(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.sqlite", DENVER)


# --- the chain ----------------------------------------------------------------------------------


def test_canonical_text_is_stable_and_refuses_floats() -> None:
    assert canonical({"b": 1, "a": [True, None, "é"]}) == '{"a":[true,null,"é"],"b":1}'
    with pytest.raises(LedgerError, match="float"):
        canonical({"price": 0.5})
    with pytest.raises(LedgerError):
        canonical({"when": object()})


def test_records_chain_and_verify(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    first = ledger.append(Entry("deposit", T0, "deposit", "d1", cash_e6=100_000_000))
    second = ledger.append(Entry("app", T0 + 1, "app", fields={"action": "engine_started"}))
    assert (first, second) == (1, 2)
    bodies = list(ledger.records())
    assert bodies[0]["prev_hash"] == GENESIS
    assert bodies[1]["prev_hash"] == digest(canonical(bodies[0]))
    assert bodies[0]["time_utc"] == "2026-01-01T00:00:00.000000000Z"
    assert bodies[0]["time_local"] == "2025-12-31T17:00:00-07:00"  # the tax zone's year
    result = ledger.verify()
    assert result.ok and result.records == 2 and result.head == ledger.head()
    assert ledger.verify("paper").ok and ledger.verify("paper").records == 0
    ledger.close()


def test_the_store_refuses_edits_and_deletions(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    ledger.append(Entry("deposit", T0, "deposit", "d1", cash_e6=1))
    ledger.close()
    db = sqlite3.connect(tmp_path / "ledger.sqlite")
    for statement in ("UPDATE record SET kind = 'fee'", "DELETE FROM record"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(statement)
    db.close()


def verify_once(tmp_path: Path) -> Verified:
    ledger = ledger_at(tmp_path)
    try:
        return ledger.verify()
    finally:
        ledger.close()


def tamper(path: Path, sql: str) -> None:
    """What someone with the file could do: drop the guards, change a row."""
    db = sqlite3.connect(path)
    db.execute("DROP TRIGGER record_no_update")
    db.execute("DROP TRIGGER record_no_delete")
    db.execute(sql)
    db.commit()
    db.close()


@pytest.mark.parametrize(
    ("sql", "problem"),
    [
        ("UPDATE record SET body = replace(body, '100000000', '900000000') WHERE seq = 1",
         "record 1 does not match its hash"),
        ("DELETE FROM record WHERE seq = 2", "sequence 2 is missing"),
        ("UPDATE record SET kind = 'fee' WHERE seq = 3", "record 3's columns differ"),
        ("UPDATE record SET body = body || ' ' WHERE seq = 1", "record 1 is not canonical"),
        ("""UPDATE record SET body = replace(body, '"cash_e6":100000000', '"cash_e6":1.5')"""
         " WHERE seq = 1", "record 1 is not canonical"),  # a float slipped in
    ],
)  # fmt: skip
def test_tampering_is_caught(tmp_path: Path, sql: str, problem: str) -> None:
    ledger = ledger_at(tmp_path)
    for n in range(3):
        ledger.append(Entry("deposit", T0 + n, "deposit", f"d{n}", cash_e6=100_000_000))
    ledger.close()
    tamper(tmp_path / "ledger.sqlite", sql)
    result = verify_once(tmp_path)
    assert not result.ok and result.problem is not None and result.problem.startswith(problem)


def test_a_rebuilt_record_with_a_valid_hash_still_breaks_the_link(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    for n in range(2):
        ledger.append(Entry("deposit", T0 + n, "deposit", f"d{n}", cash_e6=100_000_000))
    body = next(ledger.records())
    ledger.close()
    forged = canonical({**body, "cash_e6": 900_000_000})
    update = f"UPDATE record SET body = '{forged}', hash = '{digest(forged)}' WHERE seq = 1"
    tamper(tmp_path / "ledger.sqlite", update)
    assert verify_once(tmp_path).problem == "record 2 does not follow record 1"


def test_idempotence_kinds_streams_and_checkpoints(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    assert ledger.append(Entry("fill", T0, "fill", "f1")) == 1
    assert ledger.append(Entry("fill", T0, "fill", "f1")) is None  # already in
    assert ledger.append(Entry("fill", T0, "fill", "f1"), stream="paper") == 1  # its own chain
    with pytest.raises(LedgerError, match="kind"):
        ledger.append(Entry("gift", T0, "x"))
    with pytest.raises(LedgerError, match="stream"):
        ledger.append(Entry("fill", T0, "fill", "f2"), stream="dream")
    with pytest.raises(LedgerError):  # a float rolls the whole batch back
        ledger.append_all([Entry("fill", T0, "fill", "f3"), Entry("fee", T0, "x", cash_e6=0.5)])  # type: ignore[arg-type]
    assert ledger.count() == 1 and ledger.has("fill", "f1") and not ledger.has("fill", "f3")
    path = ledger.checkpoint(tmp_path / "checkpoints", T0 + 5)
    assert path.name == "2026-01-01.json" and path.parent.name == "2026"
    point = json.loads(path.read_text(encoding="utf-8"))
    assert point["seq"] == 1 and point["hash"] == ledger.head().hash
    ledger.close()


# --- the cash rules -----------------------------------------------------------------------------


def fill(
    side: str, count: str, yes: str, fee: str = "0.0100", fill_id: str = "f", ticker: str = "M"
) -> Fill:
    no = f"{1 - float(yes):.4f}"
    return Fill.model_validate({
        "fill_id": fill_id, "order_id": "o", "ticker": ticker, "outcome_side": side,
        "count_fp": count, "yes_price_dollars": yes, "no_price_dollars": no,
        "fee_cost": fee, "is_taker": True, "created_time": "2026-01-01T00:00:00Z",
    })  # fmt: skip


def test_fill_cash_follows_kalshi_netting() -> None:
    # buy 10 YES at 40c from flat: pay 4.00 and the fee
    assert fill_cash(fill("yes", "10.00", "0.4000"), 0) == (-4_010_000, 0, 1000)
    # holding 10 NO, 15 YES at 40c: 10 NO close at 60c (+6.00), 5 YES open (-2.00)
    assert fill_cash(fill("yes", "15.00", "0.4000"), -1000) == (3_990_000, 1000, 500)
    # holding 10 YES, sell 4 (NO exposure) at a YES price of 55c: receive 2.20
    assert fill_cash(fill("no", "4.00", "0.5500"), 1000) == (2_190_000, 400, 0)
    # from flat, NO exposure opens NO at the NO price
    assert fill_cash(fill("no", "1.00", "0.5500", fee="0"), 0) == (-450_000, 0, 100)


def test_an_amount_finer_than_a_millionth_is_refused() -> None:
    assert value_e6(400_000, 1000) == 4_000_000
    with pytest.raises(LedgerError, match="millionth"):
        value_e6(1, 1)


# --- the import ---------------------------------------------------------------------------------


def page[M](items: list[M], raws: list[dict[str, Any]]) -> AsyncIterator[TypedPage[M]]:
    async def pages() -> AsyncIterator[TypedPage[M]]:
        yield TypedPage(items, raws, None, None)

    return pages()


class FakeAccount:
    """The account reads the importer makes, over made-up records."""

    def __init__(self) -> None:
        self.live: list[dict[str, Any]] = []
        self.historical: list[dict[str, Any]] = []
        self.settled: list[dict[str, Any]] = []
        self.deposits_: list[dict[str, Any]] = []
        self.withdrawals_: list[dict[str, Any]] = []
        self.moves: list[dict[str, Any]] = []
        self.balance_cents = 0
        self.held: list[dict[str, Any]] = []
        self.archived: list[dict[str, Any]] = []
        self.calls: list[tuple[str, Any]] = []

    def fills_(self, rows: list[dict[str, Any]]) -> AsyncIterator[TypedPage[Fill]]:
        return page([Fill.model_validate(r) for r in rows], rows)

    def fills(
        self, *, min_ts: int | None = None, historical: bool = False
    ) -> AsyncIterator[TypedPage[Fill]]:
        self.calls.append(("historical" if historical else "fills", min_ts))
        return self.fills_(self.historical if historical else self.live)

    def settlements(self, *, min_ts: int | None = None) -> AsyncIterator[TypedPage[Settlement]]:
        self.calls.append(("settlements", min_ts))
        return page([Settlement.model_validate(r) for r in self.settled], self.settled)

    def deposits(self) -> AsyncIterator[TypedPage[Transfer]]:
        return page([Transfer.model_validate(r) for r in self.deposits_], self.deposits_)

    def withdrawals(self) -> AsyncIterator[TypedPage[Transfer]]:
        return page([Transfer.model_validate(r) for r in self.withdrawals_], self.withdrawals_)

    def instance_transfers(self) -> AsyncIterator[TypedPage[InstanceTransfer]]:
        return page([InstanceTransfer.model_validate(r) for r in self.moves], self.moves)

    async def balance(self) -> Balance:
        cents = self.balance_cents
        return Balance.model_validate({"balance": cents, "balance_dollars": f"{cents / 100:.2f}",
                                       "portfolio_value": 0, "updated_ts": 1})  # fmt: skip

    async def positions(self, *, historical: bool = False) -> Positions:
        rows = self.archived if historical else self.held
        return Positions(
            market_positions=[MarketPosition.model_validate(p) for p in rows], event_positions=[]
        )


def raw_fill(
    fill_id: str, side: str, count: str, yes: str, at: str, ticker: str = "KXT-26JAN01-A"
) -> dict[str, Any]:
    return {"fill_id": fill_id, "order_id": "o-" + fill_id, "ticker": ticker, "outcome_side": side,
            "count_fp": count, "yes_price_dollars": yes,
            "no_price_dollars": f"{1 - float(yes):.4f}",
            "fee_cost": "0.0200", "is_taker": True, "created_time": at}  # fmt: skip


def transfer(
    id_: str, cents: int, status: str = "applied", at: int = 1_767_000_000
) -> dict[str, Any]:
    return {"id": id_, "status": status, "type": "ach", "amount_cents": cents, "fee_cents": 0,
            "created_ts": at}  # fmt: skip


def position(ticker: str, count: str) -> dict[str, Any]:
    return {"ticker": ticker, "exchange_index": 0, "position_fp": count,
            "total_traded_dollars": "0", "market_exposure_dollars": "0",
            "realized_pnl_dollars": "0", "fees_paid_dollars": "0",
            "last_updated_ts": "2026-01-01T00:00:00Z"}  # fmt: skip


def account_with_history() -> FakeAccount:
    account = FakeAccount()
    account.deposits_ = [transfer("dep-1", 10_000)]  # $100
    account.historical = [raw_fill("h1", "yes", "10.00", "0.4000", "2025-12-30T10:00:00Z")]
    account.live = [
        raw_fill("h1", "yes", "10.00", "0.4000", "2025-12-30T10:00:00Z"),  # in both lists
        raw_fill("f2", "no", "4.00", "0.5500", "2025-12-31T10:00:00Z"),
    ]
    # cash: +100.00, -4.02 (buy 10 YES at 40c), +2.18 (sell 4 YES at 55c, fee 2c)
    account.balance_cents = 9_816
    account.held = [position("KXT-26JAN01-A", "6.00")]
    return account


async def test_the_first_import_takes_the_whole_history_once_and_reconciles(tmp_path: Path) -> None:
    ledger, account = ledger_at(tmp_path), account_with_history()
    importer = AccountImporter(ledger, account, VirtualClock(T0), lambda t: {"title": "Made up"})  # type: ignore[arg-type]
    report = await importer.run_once()
    assert report.added == {"deposit": 1, "fill": 2}
    assert report.reconciliation is not None and report.reconciliation.exact
    assert ("historical", None) in account.calls
    bodies = [b for b in ledger.records() if b["kind"] == "fill"]
    assert [b["fields"]["position_before_e2"] for b in bodies] == [0, 1000]
    assert bodies[0]["context"] == {"title": "Made up"}
    assert json.loads(bodies[0]["raw"])["fill_id"] == "h1"  # Kalshi's JSON, verbatim
    # a second import adds nothing; it reads fills, live and archived, from a day before the
    # newest fill, and settlements from a day before the newest settlement (none yet: all); the
    # unchanged reconciliation is not written again
    again = await importer.run_once()
    assert again.added == {} and ledger.count(kind="reconciliation") == 1
    since = 1_767_175_200 - 86_400  # a day before 2025-12-31 10:00 UTC
    assert account.calls[-3:] == [("fills", since), ("historical", since), ("settlements", None)]
    assert ledger.verify().ok
    ledger.close()


async def test_differences_are_recorded_never_fixed(tmp_path: Path) -> None:
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.balance_cents += 42  # interest Kalshi does not report
    account.held = [position("KXT-26JAN01-A", "6.00"), position("OTHER", "-3.00")]
    report = await AccountImporter(ledger, account, VirtualClock(T0)).run_once()  # type: ignore[arg-type]
    result = report.reconciliation
    assert result is not None and not result.exact
    assert result.difference_e6 == 420_000 and result.positions == {"OTHER": (0, -300)}
    seq = annotate(ledger, "interest", 420_000, T0, "November interest")
    account.held = account.held[:1]
    assert (await AccountImporter(ledger, account, VirtualClock(T0)).reconcile()).exact  # type: ignore[arg-type]
    assert ledger.count(kind="reconciliation") == 2 and seq < ledger.head().seq
    with pytest.raises(LedgerError, match="note"):
        annotate(ledger, "interest", 1, T0, " ")
    with pytest.raises(LedgerError, match="one of"):
        annotate(ledger, "fill", 1, T0, "no")
    with pytest.raises(LedgerError, match="no record 99"):
        annotate(ledger, "adjustment", 1, T0, "fix", corrects=99)
    ledger.close()


async def test_settlements_transfers_and_reversals(tmp_path: Path) -> None:
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.settled = [{
        "ticker": "KXT-26JAN01-A", "event_ticker": "KXT-26JAN01", "exchange_index": 0,
        "market_result": "yes", "yes_count_fp": "6.00", "yes_total_cost_dollars": "2.40",
        "no_count_fp": "0.00", "no_total_cost_dollars": "0", "revenue": 600, "fee_cost": "0.0400",
        "settled_time": "2026-01-01T12:00:00Z",
    }]  # fmt: skip
    account.withdrawals_ = [transfer("w-1", 1_000, "pending")]
    account.deposits_.append(transfer("dep-2", 5_000, "pending"))  # not counted until applied
    account.moves = [{"transfer_id": "m-1", "source": "event_contract", "destination": "margined",
                      "source_exchange_shard": 0, "destination_exchange_shard": 1,
                      "amount": "1.00", "status": "complete",
                      "created_ts": 1_767_100_000}]  # fmt: skip
    account.balance_cents = 9_816 + 600 - 1_000 - 100
    account.held = []
    report = await AccountImporter(ledger, account, VirtualClock(T0)).run_once()  # type: ignore[arg-type]
    assert report.added == {
        "deposit": 1,
        "fill": 2,
        "settlement": 1,
        "withdrawal": 1,
        "transfer": 1,
    }
    assert report.reconciliation is not None and report.reconciliation.exact
    account.withdrawals_ = [transfer("w-1", 1_000, "failed")]
    account.deposits_[1] = transfer("dep-2", 5_000, "applied")
    account.balance_cents += 1_000 + 5_000
    later = await AccountImporter(ledger, account, VirtualClock(T0)).run_once()  # type: ignore[arg-type]
    assert later.added == {"adjustment": 1, "deposit": 1}
    assert later.reconciliation is not None and later.reconciliation.exact
    assert replay(ledger).held() == {}
    ledger.close()


# --- the service, the command line and privacy --------------------------------------------------


async def test_the_service_records_its_actions_and_reports_counts(tmp_path: Path) -> None:
    ledger, account = ledger_at(tmp_path), account_with_history()
    clock = VirtualClock(T0)
    zone = taxzone.TaxZone(DENVER, "workstation")
    service = LedgerService(
        ledger, AccountImporter(ledger, account, clock), tmp_path / "cp", clock, zone,  # type: ignore[arg-type]
        key_fingerprint="f" * 16, settings_sha256="s1", every_s=3600,  # made up
    )  # fmt: skip
    await service.start()
    await service.import_once()
    await service.stop()
    actions = [b["fields"]["action"] for b in ledger.records(kind="app")]
    assert actions == ["engine_started", "key_loaded", "settings_changed", "engine_stopped"]
    health = service.health()
    assert health["verified"] and health["reconciled_exact"] and health["position_differences"] == 0
    assert health["errors"] == 0 and (tmp_path / "cp" / "2026" / "2026-01-01.json").exists()
    again = LedgerService(ledger, AccountImporter(ledger, account, clock), tmp_path / "cp", clock,  # type: ignore[arg-type]
                          zone, settings_sha256="s1", every_s=3600)  # fmt: skip
    await again.start()
    await again.stop()  # the same settings: no second "settings_changed"
    assert [b["fields"]["action"] for b in ledger.records(kind="app")].count(
        "settings_changed"
    ) == 1
    shown = status(ledger, zone)
    assert shown["head_seq"] == ledger.head().seq and shown["last_reconciliation"]["exact"]  # type: ignore[index]
    ledger.close()


async def test_a_broken_chain_stops_every_append(tmp_path: Path) -> None:
    ledger = ledger_at(tmp_path)
    for n in range(2):
        ledger.append(Entry("deposit", T0 + n, "deposit", f"d{n}", cash_e6=1))
    ledger.close()
    tamper(tmp_path / "ledger.sqlite", "DELETE FROM record WHERE seq = 1")
    ledger = ledger_at(tmp_path)
    clock = VirtualClock(T0)
    service = LedgerService(ledger, AccountImporter(ledger, FakeAccount(), clock), tmp_path,  # type: ignore[arg-type]
                            clock, taxzone.TaxZone(DENVER, "setting"))  # fmt: skip
    await service.start()
    await service.stop()
    assert not service.writable and service.health()["verified"] is False
    assert ledger.count() == 1  # nothing appended on top of the break
    ledger.close()


async def test_a_failed_import_is_counted_without_its_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    class Failing(FakeAccount):
        async def balance(self) -> Balance:
            raise RuntimeError("balance was 123.45")

    ledger, clock = ledger_at(tmp_path), VirtualClock(T0)
    service = LedgerService(ledger, AccountImporter(ledger, Failing(), clock), tmp_path, clock,  # type: ignore[arg-type]
                            taxzone.TaxZone(DENVER, "setting"))  # fmt: skip
    with caplog.at_level(logging.DEBUG):
        await service.import_once()
    assert service.errors == 1
    logged = " ".join(json.dumps(r.__dict__, default=str) for r in caplog.records)
    assert "123.45" not in logged and '"error": "RuntimeError"' in logged
    ledger.close()


async def test_account_values_never_reach_a_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Deferred from M1.4a: the import logs counts, never amounts, tickers or ids."""
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.balance_cents += 7_777
    with caplog.at_level(logging.DEBUG):
        await AccountImporter(ledger, account, VirtualClock(T0)).run_once()  # type: ignore[arg-type]
    logged = caplog.text + " ".join(json.dumps(r.__dict__, default=str) for r in caplog.records)
    for secret in ("KXT-26JAN01-A", "h1", "dep-1", "77.77", "77770000", "4.02", "0.4000"):
        assert secret not in logged
    ledger.close()


# --- the tax zone and the settings --------------------------------------------------------------


def test_the_tax_zone_follows_the_setting_then_the_workstation() -> None:
    assert taxzone.resolve("Europe/London").source == "setting"
    found = taxzone.resolve("", lambda: "Mountain Standard Time")
    assert (found.name, found.source) == ("America/Denver", "workstation")
    assert taxzone.resolve("", lambda: "Nowhere Standard Time").source == "fallback"
    assert taxzone.resolve("", lambda: None).name == "UTC"
    with pytest.raises(ValueError, match="not a time zone"):
        taxzone.resolve("Mars/Olympus")
    assert taxzone.windows_zone_name() is None or isinstance(taxzone.windows_zone_name(), str)


def test_settings_v2_follow_the_workstation_unless_a_zone_was_chosen() -> None:
    assert migrate({"version": 2, "tax_time_zone": "America/New_York"})["tax_time_zone"] == ""
    assert (
        migrate({"version": 2, "tax_time_zone": "Europe/London"})["tax_time_zone"]
        == "Europe/London"
    )
    assert migrate({"version": 2})["tax_time_zone"] == ""


def test_the_ledger_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ikars_engine.__main__ import main

    root = ["python -m ikars_engine", "ledger", "--root", str(tmp_path)]
    assert main([*root, "verify"]) == 0
    assert json.loads(capsys.readouterr().out)["real"] == {
        "ok": True, "records": 0, "head_seq": 0, "problem": None,
    }  # fmt: skip
    note = ["annotate", "--kind", "interest", "--amount", "0.42", "--time", "2026-01-01"]
    assert main([*root, *note, "--note", "made-up interest"]) == 0
    assert json.loads(capsys.readouterr().out) == {"appended": 1}
    assert main([*root, "status"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["records"] == {"interest": 1} and shown["last_reconciliation"] is None
    ledger = Ledger(tmp_path / "ledger" / "ledger.sqlite", DENVER)
    body = next(ledger.records())
    ledger.close()
    assert body["cash_e6"] == 420_000 and body["note"] == "made-up interest"
    tamper(tmp_path / "ledger" / "ledger.sqlite", "UPDATE record SET kind = 'fee'")
    assert main([*root, "verify"]) == 1
    assert "columns differ" in capsys.readouterr().out
    assert main([*root, *note, "--note", "on a broken chain"]) == 1  # refused, nothing appended
    assert "broken" in capsys.readouterr().out


def test_the_runner_describes_markets_and_digests_settings() -> None:
    from ikars_engine.kalshi.discovery import MarketIndex
    from ikars_engine.kalshi.runner import describer, settings_digest
    from ikars_engine.settings import Settings

    index = MarketIndex()
    assert describer(index)("NONE") is None
    assert settings_digest(Settings()) == settings_digest(Settings())
    assert settings_digest(Settings()) != settings_digest(Settings(tax_time_zone="UTC"))


class FakeMarkets:
    """Market results: a 404 on the live route sends the importer to the archive."""

    def __init__(self, result: str) -> None:
        self.result = result

    async def market(self, ticker: str) -> Any:
        from ikars_core.rest import KalshiAPIError

        raise KalshiAPIError(404, "not found")

    async def historical_market(self, ticker: str) -> Any:
        from datetime import UTC, datetime
        from types import SimpleNamespace

        return SimpleNamespace(ticker=ticker, status="finalized", result=self.result,
                               title="Made up", event_ticker="KXT-26JAN01",
                               settlement_ts=datetime(2026, 1, 1, 12, tzinfo=UTC))  # fmt: skip


def archived(realized: str) -> dict[str, Any]:
    return {**position("KXT-26JAN01-A", "0.00"), "realized_pnl_dollars": realized,
            "fees_paid_dollars": "0.04"}  # fmt: skip


async def test_an_archived_settlement_is_derived_only_when_kalshi_confirms_it(
    tmp_path: Path,
) -> None:
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.held = []  # Kalshi: flat, and the settlement is archived (no settlement record)
    # traded: -4.00 + 2.20 before fees; 6 YES win $6.00: realised 4.20
    account.archived = [archived("4.20")]
    account.balance_cents = 9_816 + 600
    importer = AccountImporter(ledger, account, VirtualClock(T0), markets=FakeMarkets("yes"))  # type: ignore[arg-type]
    report = await importer.run_once()
    assert report.added["settlement"] == 1
    assert report.reconciliation is not None and report.reconciliation.exact
    body = ledger.last("settlement")
    assert body is not None and body["fields"]["derived"] and body["cash_e6"] == 6_000_000
    assert json.loads(body["raw"])["market"]["result"] == "yes"
    assert (await importer.run_once()).added == {}  # once
    ledger.close()


@pytest.mark.parametrize(
    ("result", "realized", "markets"),
    [("yes", "4.19", True), ("scalar", "4.20", True), ("yes", "4.20", False)],
)
async def test_an_unconfirmed_archived_settlement_stays_a_difference(
    tmp_path: Path, result: str, realized: str, markets: bool
) -> None:
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.held, account.archived = [], [archived(realized)]
    account.balance_cents = 9_816 + 600
    fake = FakeMarkets(result) if markets else None
    importer = AccountImporter(ledger, account, VirtualClock(T0), markets=fake)  # type: ignore[arg-type]
    report = await importer.run_once()
    assert "settlement" not in report.added
    assert report.reconciliation is not None and not report.reconciliation.exact
    assert report.reconciliation.positions == {"KXT-26JAN01-A": (600, 0)}
    ledger.close()


async def test_a_settlement_s_fee_is_not_charged_twice(tmp_path: Path) -> None:
    """Live finding: a settlement's fee_cost repeats the fills' fees; its cash is the revenue."""
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.settled = [{
        "ticker": "KXT-26JAN01-A", "event_ticker": "KXT-26JAN01", "exchange_index": 0,
        "market_result": "yes", "yes_count_fp": "6.00", "yes_total_cost_dollars": "2.40",
        "no_count_fp": "0.00", "no_total_cost_dollars": "0", "revenue": 600,
        "fee_cost": "0.0400", "settled_time": "2026-01-01T12:00:00Z",
    }]  # fmt: skip
    account.held, account.balance_cents = [], 9_816 + 600
    report = await AccountImporter(ledger, account, VirtualClock(T0)).run_once()  # type: ignore[arg-type]
    assert report.reconciliation is not None and report.reconciliation.exact
    body = ledger.last("settlement")
    assert body is not None and body["cash_e6"] == 6_000_000 and body["fields"]["fee_e6"] == 40_000
    ledger.close()


# --- the M1.5a merge review's findings ----------------------------------------------------------


async def test_a_listed_settlement_after_an_archived_one_is_not_paid_twice(tmp_path: Path) -> None:
    """Review: an archive-derived settlement and a late listed one describe the same payout."""
    ledger, account = ledger_at(tmp_path), account_with_history()
    account.held, account.archived = [], [archived("4.20")]
    account.balance_cents = 9_816 + 600
    importer = AccountImporter(ledger, account, VirtualClock(T0), markets=FakeMarkets("yes"))  # type: ignore[arg-type]
    await importer.run_once()
    account.settled = [{
        "ticker": "KXT-26JAN01-A", "event_ticker": "KXT-26JAN01", "exchange_index": 0,
        "market_result": "yes", "yes_count_fp": "6.00", "yes_total_cost_dollars": "2.40",
        "no_count_fp": "0.00", "no_total_cost_dollars": "0", "revenue": 600,
        "fee_cost": "0.0400", "settled_time": "2026-01-01T12:00:00Z",
    }]  # fmt: skip
    later = await importer.run_once()
    assert later.added == {} and ledger.count(kind="settlement") == 1
    assert later.reconciliation is not None and later.reconciliation.exact
    ledger.close()


async def test_the_first_import_mark_survives_a_later_failure(tmp_path: Path) -> None:
    """Review: a failure after the history was in kept every later run reading it all again."""

    class FailsAfterHistory(FakeAccount):
        async def positions(self, *, historical: bool = False) -> Positions:
            raise RuntimeError("positions unavailable")

    ledger, account = ledger_at(tmp_path), account_with_history()
    failing = FailsAfterHistory()
    failing.__dict__.update(account.__dict__)
    with pytest.raises(RuntimeError):
        await AccountImporter(ledger, failing, VirtualClock(T0)).run_once()  # type: ignore[arg-type]
    assert ledger.count(kind="fill") == 2 and ledger.bookmark("import.first_done") is not None
    ledger.close()


async def test_titles_come_from_market_data_when_the_index_lacks_them(tmp_path: Path) -> None:
    """Review: records first imported without the live index never had a title."""
    ledger, account = ledger_at(tmp_path), account_with_history()
    importer = AccountImporter(ledger, account, VirtualClock(T0), markets=FakeMarkets("yes"))  # type: ignore[arg-type]
    await importer.run_once()
    fills = list(ledger.records(kind="fill"))
    assert fills[0]["context"] == {
        "title": "Made up", "event_ticker": "KXT-26JAN01", "series_ticker": "KXT",
    }  # fmt: skip
    ledger.close()


def test_a_ledger_cut_back_behind_its_checkpoint_is_caught(tmp_path: Path) -> None:
    """Review: removing the newest records leaves a consistent chain that verify alone passes."""
    ledger = ledger_at(tmp_path)
    for n in range(3):
        ledger.append(Entry("deposit", T0 + n, "deposit", f"d{n}", cash_e6=1))
    ledger.checkpoint(tmp_path / "cp", T0)
    assert ledger.against_checkpoint(tmp_path / "cp") is None
    ledger.close()
    tamper(tmp_path / "ledger.sqlite", "DELETE FROM record WHERE seq = 3")
    ledger = ledger_at(tmp_path)
    assert ledger.verify().ok  # still consistent on its own
    assert ledger.against_checkpoint(tmp_path / "cp") == (
        "record 3, named by the checkpoint of 2026-01-01, is missing"
    )
    clock = VirtualClock(T0)
    service = LedgerService(ledger, AccountImporter(ledger, FakeAccount(), clock), tmp_path / "cp",  # type: ignore[arg-type]
                            clock, taxzone.TaxZone(DENVER, "setting"))  # fmt: skip
    assert not service.check().ok
    ledger.close()
