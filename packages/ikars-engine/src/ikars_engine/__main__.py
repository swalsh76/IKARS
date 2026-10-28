"""The engine's command line.

``python -m ikars_engine smoke``: run the engine core once, offline, and print its health. It writes
a synthetic recording in a temporary folder, replays it deterministically through a tiny built-in
counting plugin, and prints the health summary as JSON. Nothing is written outside the temporary
folder, nothing touches the network, and the result is the same on every run.

``python -m ikars_engine run [--minutes N] [--root PATH]``: the live engine on Kalshi, in the
foreground (M1.4b), with IKARS's read-only key from its two environment variables.

``python -m ikars_engine ledger {verify,status,import,annotate}``: the audit ledger (M1.5a; see
:mod:`ikars_engine.ledger_cli`).

``serve``, ``stop``, ``status``, ``watchdog``, ``install-autostart``, ``remove-autostart``, ``alerts
test``, ``open``: the engine in the background, its watchdog, workstation notifications and the web
terminal's window (M1.6, M1.8a-web; see :mod:`ikars_engine.runmode_cli`).

``python -m ikars_engine demo-api [--markets N] [--hz H] [--port P] [--seconds S]``: the API
server on made-up data, for the terminal's tests (:mod:`ikars_engine.demo_api`).
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ikars_engine.paths import Paths

from ikars_engine.clocks import VirtualClock
from ikars_engine.engine import Engine
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import PluginBase, PluginContext, PluginKind, PluginSpec
from ikars_engine.sources import ReplaySource, write_recording

START_NS = 1_767_225_600_000_000_000  # 2026-01-01 00:00:00 UTC


class Counter(PluginBase):
    spec = PluginSpec(
        name="smoke-counter",
        kind=PluginKind.SIGNAL,
        version="0",
        consumes=("tick",),
        produces=("count",),
    )

    def __init__(self) -> None:
        self.seen = 0

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        self.seen += 1
        if self.seen % 10 == 0:
            await ctx.emit(
                Item(
                    ref=Ref("count", str(self.seen)),
                    source=self.spec.name,
                    received_ns=ctx.clock.now_ns(),
                    payload={"seen": self.seen},
                )
            )


def synthetic(count: int = 100) -> list[Item]:
    return [
        Item(
            ref=Ref("tick", f"T-{n}"),
            source="synthetic",
            received_ns=START_NS + n * 1_000_000,
            published_ns=START_NS + n * 1_000_000 - 500_000,
            payload={"n": n},
        )
        for n in range(count)
    ]


async def smoke(folder: Path) -> dict[str, object]:
    recording = folder / "synthetic.parquet"
    write_recording(recording, synthetic())
    clock = VirtualClock()
    engine = Engine(clock, ReplaySource([recording], clock), deterministic=True)
    engine.host.add(Counter())
    received = await engine.run()
    return {"received": received, **engine.health()}


def _positive(text: str) -> float:
    value = float(text)
    if not value > 0:  # also refuses NaN
        raise argparse.ArgumentTypeError("must be above 0")
    return value


def before_start(paths: "Paths") -> None:
    """Before the settings are first read (which migrates them): the pre-upgrade snapshot when the
    installed program's version changed (M1.9b)."""
    from ikars_engine import upgrades

    upgrades.on_start(paths)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m ikars_engine")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("smoke", help="an offline replay through a test plugin")
    live = commands.add_parser("run", help="the live engine on Kalshi, in the foreground")
    live.add_argument("--minutes", type=float, default=None, help="stop after this long")
    live.add_argument(
        "--root", type=Path, default=None, help="the data folder (default: the usual)"
    )
    from ikars_engine import ledger_cli, runmode_cli

    ledger_cli.add_parser(commands)
    runmode_cli.add_parsers(commands)
    exporter = commands.add_parser("contracts", help="the API's JSON Schema and golden examples")
    exporter.add_argument("action", choices=["export"])
    exporter.add_argument("--to", default="contracts", help="the folder (default: ./contracts)")
    demo = commands.add_parser("demo-api", help="the API server on made-up data, for tests")
    demo.add_argument("--markets", type=int, default=100)
    demo.add_argument("--hz", type=_positive, default=10.0, help="quote changes a second")
    demo.add_argument("--port", type=int, default=0, help="0: any free port")
    demo.add_argument("--seconds", type=float, default=None, help="stop after this long")
    demo.add_argument("--first-run", action="store_true", help="no settings yet: the wizard opens")
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code or 2)
    if args.command == "smoke":
        with tempfile.TemporaryDirectory(prefix="ikars-smoke-") as folder:
            report = asyncio.run(smoke(Path(folder)))
        print(json.dumps(report, indent=2, default=str))
        return 0
    if args.command == "run":
        from ikars_engine.kalshi.runner import default_paths, run
        from ikars_engine.settings import load

        paths = default_paths(args.root)
        before_start(paths)
        try:
            health = asyncio.run(run(paths, load(paths.settings), args.minutes))
        except KeyboardInterrupt:
            return 0
        print(json.dumps(health.get("kalshi"), indent=2, default=str))
        return 0
    if args.command == "demo-api":
        from ikars_engine import demo_api

        return demo_api.main(args.markets, args.hz, args.port, args.seconds, args.first_run)
    if args.command == "contracts":
        from ikars_engine.contracts import export

        print(json.dumps({"written": len(export(Path(args.to)))}))
        return 0
    if args.command in runmode_cli.COMMANDS:
        from ikars_engine.kalshi.runner import default_paths
        from ikars_engine.settings import load

        paths = default_paths(Path(args.root) if args.root else None)
        if args.command == "serve":
            before_start(paths)
        return runmode_cli.run(args, paths, load(paths.settings))
    if args.command in ("ledger", "backup"):
        from ikars_engine.kalshi.runner import default_paths
        from ikars_engine.settings import load

        paths = default_paths(Path(args.root) if args.root else None)
        command = ledger_cli.run if args.command == "ledger" else ledger_cli.run_backup
        return command(args, paths, load(paths.settings))
    parser.print_usage(sys.stderr)
    return 2


def entry() -> None:
    """The ``ikars`` command (M1.9a)."""
    sys.exit(main(sys.argv))


if __name__ == "__main__":
    entry()
