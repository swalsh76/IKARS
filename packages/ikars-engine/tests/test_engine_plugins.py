"""Plugin containment: a plugin that raises, hangs, floods or crashes is contained (PLAN D19)."""

import asyncio
from importlib.metadata import EntryPoint
from typing import Any

import pytest
from ikars_engine import plugins as plugins_module
from ikars_engine.clocks import VirtualClock, WallClock
from ikars_engine.metrics import Metrics
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import (
    HostOptions,
    PluginBase,
    PluginContext,
    PluginError,
    PluginHost,
    PluginKind,
    PluginSpec,
    PluginState,
    discover,
)


def tick(n: int) -> Item:
    return Item(ref=Ref("tick", str(n)), source="t", received_ns=n)


class Recorder(PluginBase):
    def __init__(self, name: str, behaviour: str = "ok") -> None:
        self.spec = PluginSpec(
            name=name, kind=PluginKind.SIGNAL, version="0", consumes=("tick",), produces=("out",)
        )
        self.behaviour = behaviour
        self.seen: list[str] = []

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        if self.behaviour == "raise":
            raise RuntimeError("boom")
        if self.behaviour == "hang":
            await asyncio.sleep(10)
        self.seen.append(item.ref.id)


def host(
    options: HostOptions | None = None, clock: Any = None
) -> tuple[PluginHost, list[Item], Metrics]:
    emitted: list[Item] = []
    metrics = Metrics()

    async def emit(item: Item) -> None:
        emitted.append(item)

    return PluginHost(clock or WallClock(), emit, metrics, options), emitted, metrics


async def test_deterministic_delivery_is_in_name_order_and_survives_a_raising_plugin() -> None:
    h, _, metrics = host()
    good, bad = Recorder("b-good"), Recorder("a-bad", "raise")
    h.add(good)
    h.add(bad)
    for n in range(3):
        await h.deliver(tick(n))
    assert good.seen == ["0", "1", "2"]
    assert metrics.counters["plugin.a-bad.errors"] == 3 and len(h.gaps) == 3
    assert "RuntimeError" in h.gaps[0].reason


async def test_a_hanging_handler_times_out_and_is_skipped() -> None:
    h, _, metrics = host(HostOptions(call_timeout=0.05))
    hung, fine = Recorder("hung", "hang"), Recorder("fine")
    h.add(hung)
    h.add(fine)
    await h.deliver(tick(1))
    assert fine.seen == ["1"] and metrics.counters["plugin.hung.timeouts"] == 1
    assert h.gaps[0].reason.startswith("handle timed out")


async def test_a_full_live_inbox_drops_the_oldest_and_records_a_gap() -> None:
    h, _, metrics = host(HostOptions(queue_size=2))
    slow = Recorder("slow")
    h.add(slow)
    for n in range(5):
        h.offer(tick(n))  # nothing consumes yet: the inbox fills
    assert metrics.counters["plugin.slow.gaps"] == 3
    assert all(g.reason == "inbox full: oldest item dropped" for g in h.gaps)
    h.start()
    await asyncio.sleep(0.05)
    await h.stop()
    assert slow.seen == ["3", "4"]  # the newest survive
    assert h.state("slow") is PluginState.STOPPED


class Crasher(PluginBase):
    spec = PluginSpec(name="crasher", kind=PluginKind.SOURCE, version="0")

    def __init__(self, crashes: int) -> None:
        self.crashes = crashes
        self.runs = 0

    async def run(self, ctx: PluginContext) -> None:
        self.runs += 1
        if self.runs <= self.crashes:
            raise RuntimeError("down")


async def test_a_crashing_producer_restarts_then_finishes() -> None:
    h, _, metrics = host(HostOptions(restart_base=0.001), VirtualClock())
    plugin = Crasher(crashes=2)
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.05)
    await h.stop()
    assert plugin.runs == 3 and metrics.counters["plugin.crasher.crashes"] == 2


async def test_a_producer_that_keeps_crashing_is_paused() -> None:
    h, _, _ = host(HostOptions(restart_base=0.001, max_crashes=3), VirtualClock())
    plugin = Crasher(crashes=100)
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.05)
    assert h.state("crasher") is PluginState.PAUSED and plugin.runs == 3
    await h.stop()


async def test_a_paused_plugin_gets_no_items() -> None:
    h, _, _ = host()
    plugin = Recorder("p")
    h.add(plugin)
    h.pause("p")
    await h.deliver(tick(1))
    h.offer(tick(2))
    assert plugin.seen == [] and h.states() == {"p": PluginState.PAUSED}


async def test_a_plugin_may_emit_only_what_it_declared() -> None:
    h, emitted, _ = host()
    plugin = Recorder("r")
    h.add(plugin)
    ctx = h.context("r")
    await ctx.emit(Item(ref=Ref("out", "1"), source="r", received_ns=1))
    with pytest.raises(PluginError, match="may not emit"):
        await ctx.emit(Item(ref=Ref("recommendation", "1"), source="r", received_ns=1))
    assert [i.ref.kind for i in emitted] == ["out"]


async def test_outside_calls_get_the_plugin_timeout() -> None:
    h, _, _ = host(HostOptions(call_timeout=0.02))
    h.add(Recorder("r"))
    with pytest.raises(TimeoutError):
        await h.context("r").call(asyncio.sleep(1))
    assert await h.context("r").call(asyncio.sleep(0, result=7), seconds=1) == 7


def test_two_plugins_cannot_share_a_name() -> None:
    h, _, _ = host()
    h.add(Recorder("same"))
    with pytest.raises(PluginError, match="two plugins"):
        h.add(Recorder("same"))


def test_discovery_loads_good_plugins_and_reports_broken_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = EntryPoint("good", "tests_fake:Good", "ikars.plugins")
    bad = EntryPoint("bad", "tests_fake:Bad", "ikars.plugins")
    loaded = {"good": lambda: Recorder("good"), "bad": lambda: object()}
    monkeypatch.setattr(EntryPoint, "load", lambda self: loaded[self.name])
    monkeypatch.setattr(plugins_module, "entry_points", lambda group: [good, bad])
    found, problems = discover()
    assert [p.spec.name for p in found] == ["good"]
    assert problems == ["bad: it does not provide a PluginSpec, run and handle"]


class Poller(PluginBase):
    """A producer that would poll (and spend) forever."""

    spec = PluginSpec(name="poller", kind=PluginKind.SOURCE, version="0", paid=True)

    def __init__(self) -> None:
        self.polls = 0

    async def run(self, ctx: PluginContext) -> None:
        while True:
            self.polls += 1
            await asyncio.sleep(0.001)


async def test_pausing_cancels_a_running_producer_at_once() -> None:
    h, _, _ = host()
    plugin = Poller()
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.02)
    h.pause("poller")
    await asyncio.sleep(0.01)
    polls = plugin.polls
    await asyncio.sleep(0.03)
    assert plugin.polls == polls and h.state("poller") is PluginState.PAUSED
    await h.stop()


async def test_a_pause_during_the_restart_backoff_is_kept() -> None:
    class SlowClock(VirtualClock):
        async def sleep(self, seconds: float) -> None:
            await asyncio.sleep(0.02)

    h, _, _ = host(HostOptions(restart_base=1.0), SlowClock())
    plugin = Crasher(crashes=100)
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.005)  # the first crash: now backing off
    assert h.state("crasher") is PluginState.BACKING_OFF
    h.pause("crasher")
    await asyncio.sleep(0.05)
    assert h.state("crasher") is PluginState.PAUSED and plugin.runs == 1
    await h.stop()


async def test_a_zero_timeout_means_zero_not_the_default() -> None:
    h, _, _ = host(HostOptions(call_timeout=30))
    h.add(Recorder("r"))
    with pytest.raises(TimeoutError):
        await h.context("r").call(asyncio.sleep(0.01), seconds=0)


async def test_an_emit_is_one_hop_further_than_what_was_handled() -> None:
    h, emitted, _ = host()

    class Forward(PluginBase):
        spec = PluginSpec(
            name="f", kind=PluginKind.SIGNAL, version="0", consumes=("tick",), produces=("out",)
        )

        async def handle(self, item: Item, ctx: PluginContext) -> None:
            await ctx.emit(Item(ref=Ref("out", item.ref.id), source="f", received_ns=1))

    h.add(Forward())
    await h.deliver(Item(ref=Ref("tick", "a"), source="t", received_ns=1, hops=3))
    assert emitted[0].hops == 4


async def test_a_run_that_stayed_up_resets_the_crash_count() -> None:
    clock = VirtualClock()

    class Flaky(PluginBase):
        spec = PluginSpec(name="flaky", kind=PluginKind.SOURCE, version="0")
        runs = 0

        async def run(self, ctx: PluginContext) -> None:
            self.runs += 1
            if self.runs <= 5:
                clock.advance_to(clock.now_ns() + 120 * 10**9)  # up for two minutes, then a crash
                raise RuntimeError("blip")

    h, _, metrics = host(HostOptions(restart_base=0.001, max_crashes=2, stable_seconds=60), clock)
    plugin = Flaky()
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.05)
    await h.stop()
    # five crashes, each after a stable run: never two in a row, so never paused
    assert plugin.runs == 6 and metrics.counters["plugin.flaky.crashes"] == 5


async def test_resume_restarts_a_paused_producer() -> None:
    h, _, _ = host()
    plugin = Poller()
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.01)
    h.pause("poller")
    await asyncio.sleep(0.01)
    polls = plugin.polls
    h.resume("poller")
    h.resume("poller")  # resuming a running plugin does nothing
    await asyncio.sleep(0.02)
    assert plugin.polls > polls and h.state("poller") is PluginState.RUNNING
    await h.stop()


async def test_a_resume_right_after_a_pause_still_restarts_the_producer() -> None:
    h, _, _ = host()
    plugin = Poller()
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.01)
    h.pause("poller")
    h.resume("poller")  # before the cancelled task has run again
    polls = plugin.polls
    await asyncio.sleep(0.02)
    assert plugin.polls > polls
    await h.stop()


async def test_a_finished_producer_is_not_run_again_on_resume() -> None:
    class Once(PluginBase):
        spec = PluginSpec(name="once", kind=PluginKind.SOURCE, version="0", paid=True)
        runs = 0

        async def run(self, ctx: PluginContext) -> None:
            self.runs += 1

    h, _, _ = host()
    plugin = Once()
    h.add(plugin)
    h.start()
    await asyncio.sleep(0.01)
    h.pause("once")
    h.resume("once")
    await asyncio.sleep(0.01)
    assert plugin.runs == 1
    await h.stop()
