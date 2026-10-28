"""``python -m ikars_engine ledger ...``: the audit ledger from the command line (PLAN D18).

- ``verify``: walk both chains (real and paper); exit 1 if either is broken.
- ``status``: counts by kind, the chain head, the last reconciliation's result (no amounts).
- ``import``: one import of the account now, with IKARS's read-only key (GET only).
- ``annotate --kind interest --amount 0.42 --time 2026-10-01 --note "..."``: label money the
  import cannot explain, or correct a record (``--corrects SEQ``), as a new record.
- ``restore-drill``: restore the newest backup from the share and prove it (G14).
- ``portfolio [--summary]``: open positions, priced from market data now (GET only); the owner's
  own view, in dollars, or with ``--summary`` counts only.

``python -m ikars_engine backup {init [--share PATH],run,status}``: the share (M1.5b). ``init``
creates the ``ikars`` folder and its marker and nothing else; the share's path is saved in the
local settings and never printed.

Output is counts and pass or fail (except the owner's portfolio); amounts stay in the ledger.
"""

import argparse
import asyncio
import json
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import msgspec
from ikars_core.account import Account
from ikars_core.auth import AccountSigner
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule, fee_schedule
from ikars_core.fixedpoint import PrecisionError, from_e2, from_e6, to_e6
from ikars_core.rest import KalshiAccountClient, KalshiAPIError, KalshiPublicClient

from ikars_engine import taxzone
from ikars_engine.clocks import WallClock
from ikars_engine.ledger import KINDS, Ledger
from ikars_engine.ledger_import import AccountImporter, ns_of
from ikars_engine.ledger_service import ANNOTATION_KINDS, annotate
from ikars_engine.paths import Paths
from ikars_engine.portfolio import Portfolio, holdings, portfolio
from ikars_engine.settings import Settings, save
from ikars_engine.share import (
    Drill,
    Share,
    ShareError,
    back_up,
    configured,
    migration_copier,
    restore_drill,
)


def add_parser(commands: Any) -> None:
    ledger = commands.add_parser("ledger", help="the audit and tax ledger")
    ledger.add_argument("--root", default=None, help="the data folder (default: the usual)")
    actions = ledger.add_subparsers(dest="action", required=True)
    actions.add_parser("verify", help="check both hash chains")
    actions.add_parser("status", help="counts, the chain head and the last reconciliation")
    actions.add_parser("import", help="import the account once now (read-only key, GET only)")
    note = actions.add_parser("annotate", help="label unexplained money, or correct a record")
    note.add_argument("--kind", required=True, choices=sorted(ANNOTATION_KINDS))
    note.add_argument("--amount", required=True, help="dollars, signed (cash in is positive)")
    note.add_argument(
        "--time", required=True, help="ISO date or time; without a zone: the tax zone"
    )
    note.add_argument("--note", required=True)
    note.add_argument("--corrects", type=int, default=None, help="the record this one corrects")
    actions.add_parser("restore-drill", help="restore the newest share backup and prove it")
    held = actions.add_parser("portfolio", help="open positions, priced now from market data")
    held.add_argument("--summary", action="store_true", help="counts only, no amounts")
    backup = commands.add_parser("backup", help="backups to the NAS share")
    backup.add_argument("--root", default=None, help="the data folder (default: the usual)")
    steps = backup.add_subparsers(dest="action", required=True)
    init = steps.add_parser("init", help="create the ikars folder and its marker on the share")
    init.add_argument("--share", default=None, help="the share (saved to the local settings)")
    steps.add_parser("run", help="back up now")
    steps.add_parser("status", help="whether the share can be used, and the newest backup")


def run(args: argparse.Namespace, paths: Paths, settings: Settings) -> int:
    zone = taxzone.resolve(settings.tax_time_zone)
    # a command may be the first to open the ledger after an upgrade: its pre-migration copy goes
    # to the share as the engine's would
    copier = migration_copier(configured(settings.backup.share))
    ledger = Ledger(paths.ledger / "ledger.sqlite", zone.zone, copier)
    try:
        checkpoints = paths.ledger / "checkpoints"
        if args.action == "verify":
            results = {s: ledger.verify(s) for s in ("real", "paper")}
            problem = ledger.against_checkpoint(checkpoints) if results["real"].ok else None
            if problem is not None:
                results["real"] = replace(results["real"], ok=False, problem=problem)
            _print({s: {"ok": v.ok, "records": v.records, "head_seq": v.head.seq,
                        "problem": v.problem} for s, v in results.items()})  # fmt: skip
            return 0 if all(v.ok for v in results.values()) else 1
        if args.action == "status":
            _print(status(ledger, zone))
            return 0
        if args.action == "restore-drill":
            with tempfile.TemporaryDirectory(prefix="ikars-drill-") as scratch:
                try:
                    drill = restore_drill(Share(settings.backup.share), ledger, Path(scratch))
                except ShareError as exc:  # no share set, or it cannot be reached
                    drill = Drill(False, None, 0, str(exc))
            _print({"ok": drill.ok, "day": drill.day, "records": drill.records,
                    "problem": drill.problem})  # fmt: skip
            return 0 if drill.ok else 1
        if args.action == "portfolio":
            held = asyncio.run(_priced(ledger, settings))
            _print(_summary(held) if args.summary else _dollars(held))
            return 0
        checked = ledger.verify()
        problem = checked.problem or ledger.against_checkpoint(checkpoints)
        if problem is not None:  # never build on a broken record (as the engine refuses too)
            _print({"refused": "the ledger's chain is broken", "problem": problem})
            return 1
        if args.action == "import":
            code = asyncio.run(_import(ledger, settings))
            ledger.checkpoint(checkpoints, time.time_ns())
            return code
        try:
            moment = datetime.fromisoformat(args.time)
            amount = to_e6(args.amount)
        except (PrecisionError, ValueError) as exc:  # never a traceback (M1.R 23)
            _print({"refused": "the amount or the time cannot be read", "problem": str(exc)})
            return 2
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone.zone)
        seq = annotate(ledger, args.kind, amount, ns_of(moment), args.note, args.corrects)
        _print({"appended": seq})
        return 0
    finally:
        ledger.close()


def status(ledger: Ledger, zone: taxzone.TaxZone) -> dict[str, object]:
    last = ledger.last("reconciliation")
    reconciled = None
    if last is not None:
        fields = last["fields"]
        reconciled = {"at": last["time_utc"], "exact": fields["exact"],
                      "cash_matches": fields["difference_e6"] == 0,
                      "position_differences": len(fields["positions"])}  # fmt: skip
    return {
        "records": {k: n for k in sorted(KINDS) if (n := ledger.count(kind=k))},
        "head_seq": ledger.head().seq,
        "paper_records": ledger.count("paper"),
        "tax_zone": zone.name,
        "tax_zone_source": zone.source,
        "last_reconciliation": reconciled,
    }


async def _import(ledger: Ledger, settings: Settings) -> int:
    from ikars_engine.kalshi.runner import request_bucket

    config = KalshiConfig(environment=Environment(settings.kalshi.environment))
    signer = AccountSigner.from_environment()
    bucket, _ = await request_bucket(config, settings.feed.read_share_percent, signer)
    async with (
        KalshiAccountClient(config, signer, bucket=bucket) as client,
        KalshiPublicClient(config, bucket=bucket) as public,
    ):
        importer = AccountImporter(ledger, Account(client), WallClock(), markets=MarketData(public))
        report = await importer.run_once()
    result = report.reconciliation
    _print({
        "added": dict(report.added),
        "exact": None if result is None else result.exact,
        "cash_matches": None if result is None else result.difference_e6 == 0,
        "position_differences": None if result is None else len(result.positions),
    })  # fmt: skip
    return 0


async def _priced(ledger: Ledger, settings: Settings) -> Portfolio:
    """The portfolio priced from market data now: each held market's quote, its series' fee
    terms and category (GET only; a market that cannot be read stays unpriced)."""
    from ikars_engine.kalshi.runner import request_bucket

    config = KalshiConfig(environment=Environment(settings.kalshi.environment))
    signer = AccountSigner.from_environment()
    bucket, _ = await request_bucket(config, settings.feed.read_share_percent, signer)
    held = holdings(ledger)
    quotes: dict[str, tuple[int | None, int | None]] = {}
    fees: dict[str, FeeSchedule | None] = {}
    categories: dict[str, str | None] = {}
    async with KalshiPublicClient(config, bucket=bucket) as public:
        data = MarketData(public)
        for ticker, holding in held.lots.items():
            market = await AccountImporter.market_of(data, ticker)
            if market is not None:
                quotes[ticker] = (market.yes_bid_e6, market.yes_ask_e6)
            series = holding.context.get("series_ticker", "")
            if series and series not in fees:
                try:
                    found = await data.series(series)
                except KalshiAPIError:  # a series Kalshi no longer lists: left unpriced
                    fees[series] = categories[series] = None
                    continue
                fees[series] = fee_schedule(found.fee_type, found.fee_multiplier_e6)
                categories[series] = found.category
    return portfolio(quotes, fees.get, categories.get, held)


def _summary(held: Portfolio) -> dict[str, object]:
    priced = [p for p in held.positions if p.unrealised_e6 is not None]
    return {
        "positions": len(held.positions),
        "priced": len(priced),
        "categories": len(held.exposure("category")),
    }


def _usd(value: int | None) -> str | None:
    return None if value is None else str(from_e6(value))


def _dollars(held: Portfolio) -> dict[str, object]:
    return {
        "cash": _usd(held.cash_e6),
        "open_cost": _usd(held.cost_e6),
        "unrealised_after_exit_costs": _usd(held.unrealised_e6),
        "positions": [
            {
                "ticker": p.ticker,
                "title": p.title,
                "side": p.side,
                "contracts": str(from_e2(p.count_e2)),
                "cost": _usd(p.cost_e6),
                "exit_bid": _usd(p.exit_price_e6),
                "exit_fee": _usd(p.exit_fee_e6),
                "unrealised": _usd(p.unrealised_e6),
            }
            for p in held.positions
        ],
        "exposure_by_category": {k: _usd(v) for k, v in held.exposure("category").items()},
        "exposure_by_event": {k: _usd(v) for k, v in held.exposure("event_ticker").items()},
    }


def run_backup(args: argparse.Namespace, paths: Paths, settings: Settings) -> int:
    try:
        return _backup(args, paths, settings)
    except ShareError as exc:  # its messages never name the share
        _print({"ok": False, "problem": str(exc)})
        return 1


def _backup(args: argparse.Namespace, paths: Paths, settings: Settings) -> int:
    if args.action == "init":
        target = args.share or settings.backup.share
        created = Share(target).init(time.time_ns())  # the share is checked before it is saved
        if args.share:
            backup = msgspec.structs.replace(settings.backup, share=args.share)
            save(msgspec.structs.replace(settings, backup=backup), paths.settings)
        _print({"created": created, "share_saved": bool(args.share)})
        return 0
    share = Share(settings.backup.share)
    if args.action == "status":
        problem = share.problem()
        newest = share.newest_daily() if problem is None else None
        _print({"problem": problem, "newest_daily": None if newest is None else newest.name})
        return 0
    report = back_up(
        share,
        ledger=paths.ledger / "ledger.sqlite",
        state=paths.data / "state.sqlite",
        journal=paths.data / "journal.sqlite",
        settings=paths.settings,
        checkpoints=paths.ledger / "checkpoints",
        staging=paths.data / "backup-staging",
        now_ns=time.time_ns(),
    )
    _print({
        "day": report.day,
        "head_seq": report.ledger_head.seq,
        "checkpoints_sent": report.checkpoints,
        "pruned": report.pruned,
    })  # fmt: skip
    return 0


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))
