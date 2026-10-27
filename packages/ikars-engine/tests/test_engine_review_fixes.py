"""The merge review's findings for M1.4a, each held by a test (BUILD-LOG 1.4a, Review)."""

import asyncio
import io
import logging
from pathlib import Path

import pytest
from ikars_engine import __main__ as cli
from ikars_engine.clocks import VirtualClock, WallClock
from ikars_engine.costs import CostTracker
from ikars_engine.engine import Engine
from ikars_engine.logs import REDACTED, Redactor, configure
from ikars_engine.metrics import Metrics
from ikars_engine.model import Item, Ref
from ikars_engine.plugins import (
    HostOptions,
    PluginBase,
    PluginContext,
    PluginHost,
    PluginKind,
    PluginSpec,
)
from ikars_engine.settings import VERSION, SettingsError, backup, load, migrate
from ikars_engine.sources import LiveSource

JAN_NS = 1_767_225_600_000_000_000


def tick(n: int) -> Item:
    return Item(ref=Ref("tick", str(n)), source="t", received_ns=n)


# 1. stopping the engine never drops queued items silently


async def test_items_left_queued_at_stop_are_recorded_as_a_gap() -> None:
    class Idle(PluginBase):
        spec = PluginSpec(name="idle", kind=PluginKind.SIGNAL, version="0", consumes=("tick",))

    async def emit(item: Item) -> None:
        return None

    h = PluginHost(WallClock(), emit, Metrics(), HostOptions())
    h.add(Idle())
    for n in range(5):
        h.offer(tick(n))  # not started: all five stay queued
    await h.stop()
    assert [(g.reason, g.count) for g in h.gaps] == [
        ("engine stopped: queued items not handled", 5)
    ]


# 2. secrets are refused by whole words of a key name, and PEM blocks in any value


@pytest.mark.parametrize(
    "line",
    ["api_key = 'x'", "apikey = 'x'", "odds_credential = 'x'", "auth = 'x'", "key = 'x'",
     "session-cookie = 'x'", "note = '-----BEGIN PRIVATE KEY-----'", "certs = ['-----BEGIN X']"],
)  # fmt: skip
def test_more_secret_shapes_are_refused(tmp_path: Path, line: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f"version = {VERSION}\n[plugin_settings.odds]\n{line}\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="must not hold"):
        load(path)


def test_variable_names_and_ordinary_words_are_allowed(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(
        f"version = {VERSION}\n[kalshi]\nkey_id_variable = 'IKARS_KALSHI_KEY_ID'\n"
        "[plugin_settings.news]\nkeywords = ['rain', 'fed']\nauthor_filter = 'x'\n",
        encoding="utf-8",
    )
    settings = load(path)
    assert settings.plugin_settings["news"]["keywords"] == ["rain", "fed"]


# 3 and 4. redaction is case-insensitive, covers the key's file name, and every handler


def test_paths_are_redacted_in_any_case_and_by_file_name() -> None:
    env = {"IKARS_KALSHI_PRIVATE_KEY_PATH": r"C:\Users\X\Keys\IKARS-RO.pem"}
    redactor = Redactor.from_environment(env, env)
    text = redactor.text(r"open c:\users\x\keys\ikars-ro.pem failed; IKARS-RO.pem missing")
    assert "ikars-ro" not in text.lower() and text.count(REDACTED) == 2


def test_every_handler_on_the_logger_is_protected(tmp_path: Path) -> None:
    logger = logging.getLogger(f"test.protect.{tmp_path.name}")
    logger.propagate = False
    console = io.StringIO()
    early = logging.StreamHandler(console)  # added before configure, as a console would be
    logger.addHandler(early)
    env = {"IKARS_KALSHI_KEY_ID": "00000000-1111-2222-3333-444444444444"}
    file_handler = configure(tmp_path, Redactor.from_environment(env, env), logger=logger)
    logger.warning("key %s", env["IKARS_KALSHI_KEY_ID"])
    assert env["IKARS_KALSHI_KEY_ID"] not in console.getvalue() and REDACTED in console.getvalue()
    for handler in (early, file_handler):
        logger.removeHandler(handler)
        handler.close()


# 5 and 6. migrations convert money exactly and fail as settings errors


def test_fractional_dollars_migrate_exactly() -> None:
    assert (
        migrate({"version": 1, "budget": {"monthly_dollars": 12.5}})["budget"]["monthly_e6"]
        == 12_500_000
    )


@pytest.mark.parametrize(
    "bad", [{"monthly_dollars": "forty"}, {"monthly_dollars": True}, {"monthly_dollars": 0.0000001}]
)
def test_a_bad_old_amount_is_a_settings_error(bad: dict[str, object]) -> None:
    with pytest.raises(SettingsError, match="not an amount"):
        migrate({"version": 1, "budget": bad})


@pytest.mark.parametrize("version", [0, -1, "x", 1.5, True])
def test_a_bad_version_is_a_settings_error(version: object) -> None:
    with pytest.raises(SettingsError, match="version"):
        migrate({"version": version})


# 7. deterministic: a downstream plugin's time is its own


class Slow(PluginBase):
    spec = PluginSpec(name="b-slow", kind=PluginKind.SIGNAL, version="0", consumes=("mid",))

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        await asyncio.sleep(0.06)


class Fast(PluginBase):
    spec = PluginSpec(
        name="a-fast", kind=PluginKind.SIGNAL, version="0", consumes=("tick",), produces=("mid",)
    )

    async def handle(self, item: Item, ctx: PluginContext) -> None:
        await ctx.emit(Item(ref=Ref("mid", item.ref.id), source="a-fast", received_ns=1))


async def test_a_slow_downstream_plugin_does_not_time_out_the_upstream_one() -> None:
    source = LiveSource()
    await source.put(tick(1))
    await source.close()
    engine = Engine(
        VirtualClock(), source, deterministic=True, host_options=HostOptions(call_timeout=0.1)
    )
    engine.host.add(Fast())
    engine.host.add(Slow())
    await engine.run()
    assert engine.metrics.counters.get("plugin.a-fast.timeouts", 0) == 0
    assert engine.metrics.counters["plugin.a-fast.handled"] == 1
    assert engine.metrics.counters["plugin.b-slow.handled"] == 1


# 8. backups never overwrite each other


def test_two_backups_in_the_same_moment_keep_both(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("version = 2\n", encoding="utf-8")
    first, second = backup(path), backup(path)
    assert first != second and first.exists() and second.exists()


# 9. the month's total rolls over by itself


def test_the_total_rolls_over_with_the_month() -> None:
    clock = VirtualClock(JAN_NS)
    costs = CostTracker(clock, monthly_e6=10)
    costs.spend("x", 5)
    clock.advance_to(JAN_NS + 32 * 86_400 * 10**9)
    assert costs.total_e6() == 0


# 10. a listener's error is its own


async def test_a_raising_listener_neither_blocks_routing_nor_blames_the_plugin() -> None:
    source = LiveSource()
    for item in cli.synthetic(20):
        await source.put(item)
    await source.close()
    engine = Engine(VirtualClock(), source, deterministic=True)
    engine.host.add(cli.Counter())

    def broken(item: Item) -> None:
        raise RuntimeError("closed")

    engine.listeners.append(broken)
    await engine.run()
    assert engine.metrics.counters["engine.listener_errors"] == 2  # both counts emitted
    assert engine.metrics.counters.get("plugin.smoke-counter.errors", 0) == 0


async def test_a_deterministic_emit_outside_a_delivery_is_routed_at_once() -> None:
    source = LiveSource()
    await source.close()
    engine = Engine(VirtualClock(), source, deterministic=True)
    engine.host.add(Fast())
    engine.host.add(Slow())
    await engine.host.context("a-fast").emit(
        Item(ref=Ref("mid", "x"), source="a-fast", received_ns=1)
    )
    assert engine.metrics.counters["plugin.b-slow.handled"] == 1 and not engine._pending
