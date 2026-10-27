"""The engine core: one source in, plugins in the middle, items out (PLAN M1.4a).

The engine reads ``Item``s from a ``Source`` (live or a replay) and routes each to the plugins that
consume its kind. Items a plugin emits are routed the same way, so signals can build on sources and
on each other, to a fixed depth so that plugins that feed each other can never loop forever. Every
emitted item is also offered to the engine's listeners (the loopback API in M1.7).

``deterministic`` (used for replays and back-tests) hands each item to each plugin in turn and
waits, so the same recording always gives the same output. Live, each plugin works from its own
inbox and the feed never waits for a slow plugin.

After each item the cost tracker's state is applied: from the degrade threshold every plugin is
told to take its cheaper path, and at the ceiling paid plugins are paused.
"""

import logging
from collections.abc import Callable

from ikars_engine.clocks import EventClock
from ikars_engine.costs import Budget, CostTracker
from ikars_engine.metrics import Metrics, Stages
from ikars_engine.model import Item
from ikars_engine.plugins import HostOptions, PluginHost, PluginState
from ikars_engine.skew import SkewMonitor
from ikars_engine.sources import Source

MAX_DEPTH = 8  # an item this many emits away from a feed is not routed again
log = logging.getLogger("ikars_engine")

# A listener must not block: it is called inside the emitting plugin's timeout, so it hands the
# item on (for example to a bounded queue of its own) and returns at once.
Listener = Callable[[Item], None]


class Engine:
    def __init__(
        self,
        clock: EventClock,
        source: Source,
        *,
        metrics: Metrics | None = None,
        costs: CostTracker | None = None,
        skew: SkewMonitor | None = None,
        deterministic: bool = False,
        host_options: HostOptions | None = None,
    ) -> None:
        self.clock = clock
        self.source = source
        self.metrics = metrics or Metrics()
        self.costs = costs
        self.skew = skew or SkewMonitor()
        self.deterministic = deterministic
        self.listeners: list[Listener] = []
        self._budget_paused: set[str] = set()
        self.host = PluginHost(clock, self._emitted, self.metrics, host_options)

    async def _emitted(self, item: Item) -> None:
        self.metrics.count("engine.emitted")
        for listener in self.listeners:
            listener(item)
        if item.hops >= MAX_DEPTH:  # carried on the item, so it holds live as well as replayed
            self.metrics.count("engine.depth_limit")
            log.warning("emit depth limit reached", extra={"ref": str(item.ref)})
            return
        await self._route(item)

    async def _route(self, item: Item) -> None:
        if self.deterministic:
            await self.host.deliver(item)
        else:
            self.host.offer(item)

    def _apply_budget(self) -> None:
        if self.costs is None:
            return
        state = self.costs.state()
        for name, slot_state in self.host.states().items():
            ctx = self.host.context(name)
            ctx.degraded = state is not Budget.NORMAL
            if state is Budget.PAUSED and ctx.spec.paid and slot_state is not PluginState.PAUSED:
                self.host.pause(name)
                self._budget_paused.add(name)
                log.warning("paid plugin paused: monthly budget reached", extra={"plugin": name})
            elif state is not Budget.PAUSED and name in self._budget_paused:
                # the month turned (or the budget was raised): only budget pauses are lifted
                self._budget_paused.discard(name)
                self.host.resume(name)
                log.info("paid plugin resumed: budget available", extra={"plugin": name})

    async def run(self) -> int:
        """Process every item the source gives; returns how many arrived."""
        received = 0
        if not self.deterministic:
            self.host.start()
        try:
            async for item in self.source.items():
                received += 1
                self.metrics.count("engine.received")
                stages = Stages(self.metrics, "engine.item", self.clock.monotonic_ns())
                await self._route(item)
                stages.finish(self.clock.monotonic_ns())
                self._apply_budget()
        finally:
            if not self.deterministic:
                await self.host.stop()
        return received

    def health(self) -> dict[str, object]:
        return {
            "plugins": {name: str(state) for name, state in self.host.states().items()},
            "gaps": len(self.host.gaps),
            "clock": self.skew.report(),
            "budget": None
            if self.costs is None
            else {
                "state": str(self.costs.state()),
                "month": self.costs.month,
                "spent_e6": self.costs.total_e6(),
                "monthly_e6": self.costs.monthly_e6,
            },
            "metrics": self.metrics.snapshot(),
        }
