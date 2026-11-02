"""``ikars replay``: a recorded window through the signal plugins (M1.13; see :mod:`replay`).

``ikars replay --from 2026-11-02T03:00 --to 2026-11-02T04:00 [--plugins momentum] [--compare]
[--check-books] [--out FOLDER]``. A time without an offset is the workstation's local time. The
recordings, the live journal and the state are only read; the replay's journal goes to ``--out``
(default: a new folder under the system's temporary folder).
"""

import argparse
import asyncio
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from ikars_engine.clocks import exact_ns
from ikars_engine.paths import Paths


def add_parser(commands: Any) -> None:
    replay = commands.add_parser("replay", help="a recorded window through the signal plugins")
    replay.add_argument("--from", dest="start", required=True, help="local time unless an offset")
    replay.add_argument("--to", dest="end", required=True)
    replay.add_argument("--plugins", default=None, help="signal plugins, by name (default: all)")
    replay.add_argument("--warmup-min", type=int, default=40, help="replayed, not reported")
    replay.add_argument("--out", type=Path, default=None, help="the replay's journal goes here")
    replay.add_argument("--compare", action="store_true", help="against the live journal")
    replay.add_argument("--check-books", action="store_true", help="rebuilt books vs the ticker")
    replay.add_argument("--root", default=None, help="the data folder (default: the usual)")


def moment(text: str) -> int:
    """A time as given: with its offset, or else the workstation's local time (the owner's own
    wording at the console)."""
    when = datetime.fromisoformat(text)
    return exact_ns(when if when.tzinfo is not None else when.astimezone())


def run(args: argparse.Namespace, paths: Paths, plugin_settings: dict[str, Any]) -> int:
    from ikars_engine.replay import book_agreement, compare, gaps_in, recommendations, run_replay

    start, end = moment(args.start), moment(args.end)
    if end <= start:
        print(json.dumps({"error": "--to must be after --from"}))
        return 2
    out = args.out or Path(tempfile.mkdtemp(prefix="ikars-replay-"))
    if out.resolve().is_relative_to(
        paths.root.resolve()
    ):  # never the live journal (merge review 3)
        print(json.dumps({"error": "--out must be outside IKARS's own folder"}))
        return 2
    out.mkdir(parents=True, exist_ok=True)
    names = [n.strip() for n in args.plugins.split(",")] if args.plugins else None
    try:
        report = asyncio.run(
            run_replay(paths.recordings, out, (start, end), plugins=names,
                       plugin_settings=plugin_settings, warmup_ns=args.warmup_min * 60 * 10**9)
        )  # fmt: skip
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    report["gaps"] = gaps_in(paths.data / "state.sqlite", start, end)
    if args.compare:
        live = recommendations(paths.data / "journal.sqlite", start, end)
        report["compare"] = compare(live, recommendations(out / "journal.sqlite", start, end))
    if args.check_books:
        found = book_agreement(paths.recordings, start, end)

        def share(part: int, whole: int) -> float | None:
            return round(part / whole, 4) if whole else None

        report["books"] = {
            **found,
            "share": share(found["agreed"], found["compared"]),
            "settled_share": share(found["settled_agreed"], found["settled"]),
        }
    print(json.dumps(report, indent=2, default=str))
    return 0
