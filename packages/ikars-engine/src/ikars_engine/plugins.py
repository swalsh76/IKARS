"""Plugins: sources, signals, custom order types and venues (PLAN D19, plugin architecture).

A plugin is a package that registers a factory under the entry-point group ``ikars.plugins``; the
engine finds it without any change to the engine. Each plugin declares a ``PluginSpec``: its kind,
the item kinds it consumes and produces, the environment variables it needs (never the secrets
themselves) and its request rate. It may implement either or both of:

- ``run(ctx)``: a long-running producer (a source polling a feed); it emits items with
  ``ctx.emit``.
- ``handle(item, ctx)``: called for each item of a kind it consumes (a signal).

The host contains every plugin so that one failing plugin never stalls the others:

- **Live**, each plugin has its own task and a bounded inbox. When the inbox is full the oldest item
  is dropped and **a gap is recorded** (owner's choice, 2026-10-27): never a silent drop, and never
  blocking the feed.
- **Deterministic** (replays), items are handed to each plugin in turn, in a fixed order, so a
  replay gives byte-identical output.
- In both, every call has a timeout. A call that raises or times out is counted and the item is
  skipped. A producer that crashes is restarted with exponential backoff, and one that keeps
  crashing is paused.
"""

import asyncio
import contextvars
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from importlib.metadata import EntryPoint, entry_points
from typing import Any, Protocol, runtime_checkable

import msgspec

from ikars_engine.clocks import EventClock
from ikars_engine.metrics import Metrics
from ikars_engine.model import Item

ENTRY_POINT_GROUP = "ikars.plugins"
# The hops of the item a plugin is handling, per task: a live plugin's producer and consumer tasks
# share one context object, so this must not be a field on it.
_handling_hops: contextvars.ContextVar[int] = contextvars.ContextVar("handling_hops", default=0)
log = logging.getLogger("ikars_engine.plugins")


class PluginKind(StrEnum):
    SOURCE = "source"
    SIGNAL = "signal"
    ORDER_TYPE = "order_type"
    VENUE = "venue"


class PluginSpec(msgspec.Struct, frozen=True, kw_only=True):
    name: str
    kind: PluginKind
    version: str
    consumes: tuple[str, ...] = ()  # item kinds handed to handle()
    produces: tuple[str, ...] = ()  # item kinds it may emit
    secrets: tuple[str, ...] = ()  # environment variable NAMES it needs
    rate_per_second: float | None = None  # its own request budget, if it calls out
    paid: bool = False  # it spends money (LLM, paid API): paused when the budget is used up


class PluginState(StrEnum):
    RUNNING = "running"
    BACKING_OFF = "backing_off"
    PAUSED = "paused"  # crashed too often, or paused by the cost tracker
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class Gap:
    """Items a plugin never saw, and why."""

    plugin: str
    reason: str
    count: int
    at_ns: int


class PluginContext:
    """What a plugin gets from the host: the clock, its settings, emit, a timeout wrapper and a
    logger."""

    def __init__(
        self,
        spec: PluginSpec,
        clock: EventClock,
        settings: dict[str, Any],
        emit: Callable[[Item], Awaitable[None]],
        call_timeout: float,
    ) -> None:
        self.spec = spec
        self.clock = clock
        self.settings = settings
        self._emit = emit
        self._call_timeout = call_timeout
        self.log = logging.getLogger(f"ikars_engine.plugin.{spec.name}")
        self.degraded = False  # set by the cost tracker: use the cheaper path

    async def emit(self, item: Item) -> None:
        """Emit ``item``. Its ``hops`` is set to one more than the item being handled (none, for a
        producer), so the engine can stop plugins that feed each other, live or replayed."""
        if item.ref.kind not in self.spec.produces:
            raise PluginError(f"{self.spec.name} may not emit {item.ref.kind!r}")
        await self._emit(msgspec.structs.replace(item, hops=_handling_hops.get() + 1))

    async def call[T](self, awaitable: Awaitable[T], seconds: float | None = None) -> T:
        """Await an outside call within the plugin's timeout; every network call goes through it."""
        async with asyncio.timeout(self._call_timeout if seconds is None else seconds):
            return await awaitable


class PluginError(Exception):
    pass


@runtime_checkable
class Plugin(Protocol):
    spec: PluginSpec

    async def run(self, ctx: PluginContext) -> None: ...

    async def handle(self, item: Item, ctx: PluginContext) -> None: ...


class PluginBase:
    """A convenient base: override ``run``, ``handle`` or both."""

    spec: PluginSpec

    async def run(self, ctx: PluginContext) -> None:
        return None

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        return None


@dataclass(slots=True)
class _Slot:
    plugin: Plugin
    ctx: PluginContext
    inbox: asyncio.Queue[Item]
    state: PluginState = PluginState.RUNNING
    crashes: int = 0
    finished: bool = False  # its run() returned: a one-shot producer is never restarted
    tasks: list[asyncio.Task[None]] = field(default_factory=list)


def discover(group: str = ENTRY_POINT_GROUP) -> tuple[list[Plugin], list[str]]:
    """Every plugin registered under ``group``, and the reasons any could not be loaded."""
    plugins: list[Plugin] = []
    problems: list[str] = []
    found: Iterable[EntryPoint] = entry_points(group=group)
    for entry in sorted(found, key=lambda e: e.name):
        try:
            plugin = entry.load()()
            if not isinstance(plugin, Plugin) or not isinstance(plugin.spec, PluginSpec):
                raise PluginError("it does not provide a PluginSpec, run and handle")
            plugins.append(plugin)
        except Exception as exc:  # a broken plugin must never stop the engine
            problems.append(f"{entry.name}: {exc}")
            log.warning("plugin not loaded", extra={"plugin": entry.name, "reason": str(exc)})
    return plugins, problems


@dataclass(frozen=True, slots=True)
class HostOptions:
    queue_size: int = 10_000
    call_timeout: float = 30.0
    restart_base: float = 1.0
    restart_max: float = 300.0
    max_crashes: int = 10  # crashes in a row (a run that stays up stable_seconds resets the count)
    stable_seconds: float = 60.0
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)  # per plugin, by name


class PluginHost:
    def __init__(
        self,
        clock: EventClock,
        emit: Callable[[Item], Awaitable[None]],
        metrics: Metrics,
        options: HostOptions | None = None,
    ) -> None:
        o = options or HostOptions()
        self._clock = clock
        self._emit = emit
        self._metrics = metrics
        self._queue_size = o.queue_size
        self._timeout = o.call_timeout
        self._restart_base = o.restart_base
        self._restart_max = o.restart_max
        self._max_crashes = o.max_crashes
        self._stable_ns = int(o.stable_seconds * 1e9)
        self._started = False
        self._settings = o.settings
        self._slots: dict[str, _Slot] = {}
        self.gaps: list[Gap] = []

    # --- registration ----------------------------------------------------------------------

    def add(self, plugin: Plugin) -> None:
        name = plugin.spec.name
        if name in self._slots:
            raise PluginError(f"two plugins are named {name!r}")
        ctx = PluginContext(
            plugin.spec, self._clock, self._settings.get(name, {}), self._emit, self._timeout
        )
        self._slots[name] = _Slot(plugin, ctx, asyncio.Queue(self._queue_size))

    def state(self, name: str) -> PluginState:
        return self._slots[name].state

    def states(self) -> dict[str, PluginState]:
        return {name: slot.state for name, slot in sorted(self._slots.items())}

    def context(self, name: str) -> PluginContext:
        return self._slots[name].ctx

    def pause(self, name: str) -> None:
        """Pause a plugin: no more items, and a running producer is cancelled (a paid source must
        stop spending at once, not at its next crash)."""
        slot = self._slots[name]
        slot.state = PluginState.PAUSED
        for task in slot.tasks[:1]:  # the producer; the consumer goes on skipping and counting
            if not task.done():
                task.cancel()

    # --- delivery --------------------------------------------------------------------------

    def _consumers(self, item: Item) -> list[_Slot]:
        return [
            s
            for name, s in sorted(self._slots.items())
            if item.ref.kind in s.plugin.spec.consumes and s.state is not PluginState.PAUSED
        ]

    def offer(self, item: Item) -> None:
        """Live: queue ``item`` for every plugin that consumes its kind, never blocking. A full
        inbox drops its oldest item, and the drop is recorded as a gap."""
        for slot in self._consumers(item):
            if slot.inbox.full():
                slot.inbox.get_nowait()
                self._gap(slot.plugin.spec.name, "inbox full: oldest item dropped", 1)
            slot.inbox.put_nowait(item)

    async def deliver(self, item: Item) -> None:
        """Deterministic: hand ``item`` to each consuming plugin in turn, in name order."""
        for slot in self._consumers(item):
            await self._handle(slot, item)

    async def _handle(self, slot: _Slot, item: Item) -> None:
        name = slot.plugin.spec.name
        started = self._clock.monotonic_ns()
        token = _handling_hops.set(item.hops)
        try:
            async with asyncio.timeout(self._timeout):
                await slot.plugin.handle(item, slot.ctx)
        except TimeoutError:
            self._metrics.count(f"plugin.{name}.timeouts")
            self._gap(name, "handle timed out: item skipped", 1)
        except Exception as exc:
            self._metrics.count(f"plugin.{name}.errors")
            self._gap(name, f"handle raised {type(exc).__name__}: item skipped", 1)
            log.warning("plugin raised", extra={"plugin": name, "error": repr(exc)})
        else:
            self._metrics.count(f"plugin.{name}.handled")
        finally:
            _handling_hops.reset(token)
            self._metrics.observe(f"plugin.{name}.handle_ns", self._clock.monotonic_ns() - started)

    def _gap(self, plugin: str, reason: str, count: int) -> None:
        self.gaps.append(Gap(plugin, reason, count, self._clock.now_ns()))
        self._metrics.count(f"plugin.{plugin}.gaps", count)

    # --- live tasks ------------------------------------------------------------------------

    def start(self) -> None:
        self._started = True
        for slot in self._slots.values():
            slot.tasks = [
                asyncio.create_task(self._produce(slot), name=f"{slot.plugin.spec.name}.run"),
                asyncio.create_task(self._consume(slot), name=f"{slot.plugin.spec.name}.handle"),
            ]

    def resume(self, name: str) -> None:
        """Undo a pause: the plugin gets items again, and its producer is started afresh."""
        slot = self._slots[name]
        if slot.state is not PluginState.PAUSED:
            return
        slot.state = PluginState.RUNNING
        slot.crashes = 0
        if self._started and slot.tasks and not slot.finished:
            old = slot.tasks[0]
            if not old.done():
                old.cancel()  # still cancelling from the pause: replace it all the same
            slot.tasks[0] = asyncio.create_task(self._produce(slot), name=f"{name}.run")

    async def stop(self) -> None:
        tasks = [t for s in self._slots.values() for t in s.tasks]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for slot in self._slots.values():
            slot.state = PluginState.STOPPED

    async def _consume(self, slot: _Slot) -> None:
        while True:
            item = await slot.inbox.get()
            if slot.state is PluginState.PAUSED:
                self._gap(slot.plugin.spec.name, "plugin paused: item skipped", 1)
                continue
            await self._handle(slot, item)

    async def _produce(self, slot: _Slot) -> None:
        name = slot.plugin.spec.name
        while slot.state is not PluginState.PAUSED:
            began = self._clock.monotonic_ns()
            try:
                await slot.plugin.run(slot.ctx)
                slot.finished = True  # a producer that returns has finished
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._clock.monotonic_ns() - began >= self._stable_ns:
                    slot.crashes = 0  # it had been running well: this is not a crash loop
                slot.crashes += 1
                self._metrics.count(f"plugin.{name}.crashes")
                log.warning("plugin crashed", extra={"plugin": name, "error": repr(exc)})
                if slot.crashes >= self._max_crashes:
                    slot.state = PluginState.PAUSED
                    log.error("plugin paused after repeated crashes", extra={"plugin": name})
                    return
                slot.state = PluginState.BACKING_OFF
                delay = min(self._restart_max, self._restart_base * 2 ** (slot.crashes - 1))
                await self._clock.sleep(delay)
                if slot.state is PluginState.PAUSED:  # paused while backing off: stay paused
                    return
                slot.state = PluginState.RUNNING
