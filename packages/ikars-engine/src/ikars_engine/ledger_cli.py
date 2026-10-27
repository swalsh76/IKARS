"""``python -m ikars_engine ledger ...``: the audit ledger from the command line (PLAN D18).

- ``verify``: walk both chains (real and paper); exit 1 if either is broken.
- ``status``: counts by kind, the chain head, the last reconciliation's result (no amounts).
- ``import``: one import of the account now, with IKARS's read-only key (GET only).
- ``annotate --kind interest --amount 0.42 --time 2026-10-01 --note "..."``: label money the
  import cannot explain, or correct a record (``--corrects SEQ``), as a new record.

Output is counts and pass or fail; amounts stay in the ledger.
"""

import argparse
import asyncio
import json
import time
from dataclasses import replace
from datetime import datetime
from typing import Any

from ikars_core.account import Account
from ikars_core.auth import AccountSigner
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.fixedpoint import to_e6
from ikars_core.rest import KalshiAccountClient, KalshiPublicClient

from ikars_engine import taxzone
from ikars_engine.clocks import WallClock
from ikars_engine.ledger import KINDS, Ledger
from ikars_engine.ledger_import import AccountImporter, ns_of
from ikars_engine.ledger_service import ANNOTATION_KINDS, annotate
from ikars_engine.paths import Paths
from ikars_engine.settings import Settings


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


def run(args: argparse.Namespace, paths: Paths, settings: Settings) -> int:
    zone = taxzone.resolve(settings.tax_time_zone)
    ledger = Ledger(paths.ledger / "ledger.sqlite", zone.zone)
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
        checked = ledger.verify()
        problem = checked.problem or ledger.against_checkpoint(checkpoints)
        if problem is not None:  # never build on a broken record (as the engine refuses too)
            _print({"refused": "the ledger's chain is broken", "problem": problem})
            return 1
        if args.action == "import":
            code = asyncio.run(_import(ledger, settings))
            ledger.checkpoint(checkpoints, time.time_ns())
            return code
        moment = datetime.fromisoformat(args.time)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=zone.zone)
        seq = annotate(
            ledger, args.kind, to_e6(args.amount), ns_of(moment), args.note, args.corrects
        )
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


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))
