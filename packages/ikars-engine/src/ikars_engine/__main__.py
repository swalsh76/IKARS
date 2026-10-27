"""The engine's command line.

``python -m ikars_engine smoke``: run the engine core once, offline, and print its health. It writes
a synthetic recording in a temporary folder, replays it deterministically through a tiny built-in
counting plugin, and prints the health summary as JSON. Nothing is written outside the temporary
folder, nothing touches the network, and the result is the same on every run.

``python -m ikars_engine run [--minutes N] [--root PATH]``: the live engine on Kalshi, in the
foreground (M1.4b), with IKARS's read-only key from its two environment variables.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m ikars_engine")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("smoke", help="an offline replay through a test plugin")
    live = commands.add_parser("run", help="the live engine on Kalshi, in the foreground")
    live.add_argument("--minutes", type=float, default=None, help="stop after this long")
    live.add_argument(
        "--root", type=Path, default=None, help="the data folder (default: the usual)"
    )
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
        try:
            health = asyncio.run(run(paths, load(paths.settings), args.minutes))
        except KeyboardInterrupt:
            return 0
        print(json.dumps(health.get("kalshi"), indent=2, default=str))
        return 0
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
