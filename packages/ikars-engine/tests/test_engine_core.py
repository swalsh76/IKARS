"""The engine end to end: deterministic replays, emits, budgets, metrics, skew and the smoke run."""

import json
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ikars_core.clock import sample_from_response
from ikars_engine import __main__ as cli
from ikars_engine.clocks import VirtualClock
from ikars_engine.costs import Budget, CostTracker
from ikars_engine.engine import MAX_DEPTH, Engine
from ikars_engine.metrics import EDGES_NS, Histogram, Metrics, Stages
from ikars_engine.model import Item, Ref, encode
from ikars_engine.plugins import PluginBase, PluginContext, PluginKind, PluginSpec, PluginState
from ikars_engine.skew import SkewMonitor
from ikars_engine.sources import LiveSource, ReplaySource, write_recording

JAN_NS = 1_767_225_600_000_000_000  # 2026-01-01 UTC


async def replay_outputs(path: Path) -> bytes:
    clock = VirtualClock()
    engine = Engine(clock, ReplaySource([path], clock), deterministic=True)
    engine.host.add(cli.Counter())
    out: list[bytes] = []

    def listen(item: Item) -> None:
        out.append(encode(item))

    engine.listeners.append(listen)
    await engine.run()
    return b"\n".join(out)


async def test_the_same_recording_replays_to_byte_identical_output(tmp_path: Path) -> None:
    path = tmp_path / "r.parquet"
    write_recording(path, cli.synthetic(200))
    first, second = await replay_outputs(path), await replay_outputs(path)
    assert first == second and first.count(b"\n") == 19  # 20 counts emitted


class Echo(PluginBase):
    """Emits every item it gets as a new item it also consumes: an endless loop if unbounded."""

    spec = PluginSpec(
        name="echo", kind=PluginKind.SIGNAL, version="0", consumes=("loop",), produces=("loop",)
    )

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        await ctx.emit(
            Item(ref=Ref("loop", item.ref.id + "+"), source="echo", received_ns=item.received_ns)
        )


async def test_plugins_feeding_each_other_stop_at_the_depth_limit() -> None:
    source = LiveSource()
    await source.put(Item(ref=Ref("loop", "x"), source="t", received_ns=1))
    await source.close()
    engine = Engine(VirtualClock(), source, deterministic=True)
    engine.host.add(Echo())
    await engine.run()
    # hops 1 to MAX_DEPTH are emitted; the one at MAX_DEPTH is not routed again
    assert engine.metrics.counters["engine.emitted"] == MAX_DEPTH
    assert engine.metrics.counters["engine.depth_limit"] == 1


async def test_the_depth_limit_holds_live_too() -> None:
    import asyncio

    source = LiveSource()
    engine = Engine(VirtualClock(), source)
    engine.host.add(Echo())
    run = asyncio.create_task(engine.run())
    await source.put(Item(ref=Ref("loop", "x"), source="t", received_ns=1))
    await asyncio.sleep(0.1)  # live: the loop runs through the inboxes
    await source.close()
    await run
    assert engine.metrics.counters["engine.emitted"] == MAX_DEPTH
    assert engine.metrics.counters["engine.depth_limit"] == 1


class Paid(PluginBase):
    spec = PluginSpec(
        name="llm", kind=PluginKind.SIGNAL, version="0", consumes=("tick",), paid=True
    )

    def __init__(self, costs: CostTracker) -> None:
        self.costs = costs
        self.degraded_seen: list[bool] = []

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        self.degraded_seen.append(ctx.degraded)
        self.costs.spend("llm", 3_000_000)


async def test_the_budget_degrades_then_pauses_paid_plugins() -> None:
    clock = VirtualClock(JAN_NS)
    costs = CostTracker(clock, monthly_e6=10_000_000, degrade_at_percent=80)
    source = LiveSource()
    for n in range(6):
        await source.put(Item(ref=Ref("tick", str(n)), source="t", received_ns=JAN_NS))
    await source.close()
    engine = Engine(clock, source, costs=costs, deterministic=True)
    plugin = Paid(costs)
    engine.host.add(plugin)
    await engine.run()
    # $3 a call against $10: each call sees the state left by the one before it; the third call
    # brings the spend to $9 (past the $8 threshold), so the fourth is degraded, and it brings the
    # spend to $12, past the ceiling, so the plugin is paused and gets no fifth call
    assert plugin.degraded_seen == [False, False, False, True]
    assert engine.host.state("llm") is PluginState.PAUSED
    health = engine.health()
    assert health["budget"] == {
        "state": "paused",
        "month": "2026-01",
        "spent_e6": 12_000_000,
        "monthly_e6": 10_000_000,
    }


async def test_live_mode_runs_end_to_end() -> None:
    source = LiveSource()
    engine = Engine(VirtualClock(), source)
    plugin = cli.Counter()
    engine.host.add(plugin)
    for item in cli.synthetic(20):
        await source.put(item)
    await source.close()
    assert await engine.run() == 20
    assert engine.metrics.counters["engine.received"] == 20


def test_costs_roll_over_with_the_month_and_refuse_bad_amounts() -> None:
    clock = VirtualClock(JAN_NS)
    costs = CostTracker(clock, monthly_e6=1_000_000)
    assert costs.spend("a", 1_000_000) is Budget.PAUSED
    clock.advance_to(JAN_NS + 32 * 86_400 * 10**9)  # February
    assert costs.state() is Budget.NORMAL and costs.by_plugin() == {} and costs.month == "2026-02"
    for bad in (-1, 1.5, True):
        with pytest.raises(ValueError):
            costs.spend("a", bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CostTracker(clock, monthly_e6=1, degrade_at_percent=0)


@given(
    st.lists(st.integers(min_value=0, max_value=10**12), min_size=1, max_size=200),
    st.floats(1, 100),
)
def test_a_percentile_is_never_optimistic(values: list[int], p: float) -> None:
    histogram = Histogram()
    for v in values:
        histogram.add(v)
    ordered = sorted(values)
    true = ordered[max(0, -(-len(ordered) * int(p) // 100) - 1)] if int(p) else ordered[0]
    reading = histogram.percentile_ns(p)
    assert reading is not None and reading >= min(true, EDGES_NS[-1]) or reading == histogram.max_ns


def test_metrics_snapshot_and_stages() -> None:
    metrics = Metrics()
    assert Histogram().percentile_ns(50) is None
    stages = Stages(metrics, "alert", 0)
    stages.mark("signal", 2_000)
    assert stages.finish(5_000) == 5_000
    metrics.count("x", 2)
    snap = metrics.snapshot()
    assert snap["counters"] == {"x": 2}
    assert snap["timings"]["alert.total_ns"]["count"] == 1  # type: ignore[index]


def test_skew_is_unmeasured_until_sampled_then_reports_an_offset() -> None:
    monitor = SkewMonitor()
    assert monitor.offset_ms == 0 and monitor.report()["status"] == "unmeasured"
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).timestamp()
    for k in range(4):
        sent = now + k * 10.3
        date = format_datetime(datetime.fromtimestamp(sent - 5, UTC), usegmt=True)
        monitor.add_sample(sample_from_response(sent, sent + 0.05, {"date": date}))
    monitor.add_frame(1_000, 990)
    report = monitor.report()
    assert report["samples"] == 4 and report["frames"] == 1 and report["frame_bound_ms"] == 10
    assert 4_000 <= monitor.offset_ms <= 6_000 and report["status"] == "skewed"


def test_the_smoke_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["ikars_engine", "smoke"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["received"] == 100 and report["gaps"] == 0
    assert cli.main(["ikars_engine"]) == 2


async def test_a_budget_pause_is_lifted_when_the_month_turns_but_a_crash_pause_is_not() -> None:
    clock = VirtualClock(JAN_NS)
    costs = CostTracker(clock, monthly_e6=1_000_000)
    costs.spend("llm", 1_000_000)  # January's budget is gone
    source = LiveSource()
    engine = Engine(clock, source, costs=costs, deterministic=True)
    engine.host.add(Paid(costs))
    engine.host.add(cli.Counter())
    engine._apply_budget()
    assert engine.host.state("llm") is PluginState.PAUSED
    engine.host.pause("smoke-counter")  # paused for another reason (say, crashes)
    clock.advance_to(JAN_NS + 32 * 86_400 * 10**9)  # February
    engine._apply_budget()
    assert engine.host.state("llm") is PluginState.RUNNING
    assert engine.host.state("smoke-counter") is PluginState.PAUSED
