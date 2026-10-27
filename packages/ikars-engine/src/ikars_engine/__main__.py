"""``python -m ikars_engine smoke``: run the engine core once, offline, and print its health.

It writes a synthetic recording in a temporary folder, replays it deterministically through a tiny
built-in counting plugin, and prints the health summary as JSON. Nothing is written outside the
temporary folder, nothing touches the network, and the result is the same on every run.
"""

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
    if argv[1:] != ["smoke"]:
        print("usage: python -m ikars_engine smoke", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="ikars-smoke-") as folder:
        report = asyncio.run(smoke(Path(folder)))
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
