"""Building and running the live engine on Kalshi (``python -m ikars_engine run``).

Everything comes from the settings and the two environment variables of IKARS's read-only key. The
REST request budget is sized from the account's own limits, read at start: a share (owner: 25%) of
the read bucket's rate and burst, shared by every REST call IKARS makes, so it backs off long before
it could crowd KAPS's ingest on the same account. The clock is sampled from REST replies, at most
once a minute, and the signer is given the measured offset.

The live engine runs in the foreground until Ctrl+C or the given number of minutes, printing a short
health line every minute. The background run mode (start at login, keep awake, watchdog) is M1.6.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path

from ikars_core.account import Account
from ikars_core.auth import AccountSigner, DataSigner
from ikars_core.clock import ClockCheckError, sample_from_response
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import KalshiAccountClient, KalshiPublicClient
from ikars_core.ws import FeedConfig, KalshiFeed

from ikars_engine.clocks import WallClock
from ikars_engine.engine import Engine
from ikars_engine.governor import GB, StorageGovernor
from ikars_engine.kalshi.discovery import Discovery, MarketIndex
from ikars_engine.kalshi.exchange import ExchangeWatch
from ikars_engine.kalshi.source import KalshiSource
from ikars_engine.kalshi.watched import WatchedSet
from ikars_engine.logs import Redactor, configure
from ikars_engine.metrics import Metrics
from ikars_engine.paths import Paths
from ikars_engine.recorder import Recorder
from ikars_engine.settings import Settings
from ikars_engine.skew import SkewMonitor
from ikars_engine.store import StateStore

log = logging.getLogger("ikars_engine.kalshi.runner")
SAMPLE_EVERY_S = 60.0


def clock_observer(skew: SkewMonitor) -> Callable[[float, float, Mapping[str, str]], None]:
    last = [0.0]

    def observe(sent: float, received: float, headers: Mapping[str, str]) -> None:
        if received - last[0] < SAMPLE_EVERY_S:
            return
        try:
            skew.add_sample(sample_from_response(sent, received, headers))
            last[0] = received
        except ClockCheckError:
            pass

    return observe


async def request_bucket(
    config: KalshiConfig, share_percent: int
) -> tuple[TokenBucket, dict[str, object]]:
    """A token bucket of ``share_percent`` of the account's read bucket (read once at start)."""
    signer = AccountSigner.from_environment()
    async with KalshiAccountClient(config, signer) as client:
        limits = await Account(client).limits()
    rate = limits.read.refill_rate * share_percent / 100
    burst = limits.read.bucket_capacity * share_percent / 100
    return TokenBucket(rate=rate, capacity=max(burst, config.request_cost_tokens)), {
        "tier": limits.usage_tier,
        "tokens_per_s": rate,
        "burst_tokens": burst,
    }


async def build(paths: Paths, settings: Settings) -> tuple[Engine, KalshiSource, StateStore]:
    config = KalshiConfig(environment=Environment(settings.kalshi.environment))
    clock = WallClock()
    metrics = Metrics()
    skew = SkewMonitor()
    bucket, budget = await request_bucket(config, settings.feed.read_share_percent)
    log.info("request budget", extra=budget)
    client = KalshiPublicClient(config, bucket=bucket, observe=clock_observer(skew))
    data = MarketData(client)
    state = StateStore(paths.data / "state.sqlite")
    signer = DataSigner.from_environment()
    feed = KalshiFeed(signer, FeedConfig(url=config.ws_url))
    watched = WatchedSet(
        max_markets=settings.feed.watched_max_markets,
        max_deltas_per_second=settings.feed.watched_max_deltas_per_second,
        pinned=set(settings.feed.watch),
    )
    source = KalshiSource(
        feed=feed,
        data=data,
        discovery=Discovery(data, MarketIndex(), state, clock),
        exchange=ExchangeWatch(data, state, clock),
        state=state,
        recorder=Recorder(paths.recordings),
        clock=clock,
        metrics=metrics,
        skew=skew,
        watched=watched,
        governor=StorageGovernor(paths.recordings, settings.feed.storage_budget_gb * GB),
        signer=signer,
    )
    engine = Engine(clock, source, metrics=metrics, skew=skew)
    engine.health_parts["kalshi"] = source.health
    engine.health_parts["request_budget"] = lambda: requests_used(budget, bucket, client)
    return engine, source, state


def requests_used(
    budget: dict[str, object], bucket: TokenBucket, client: KalshiPublicClient
) -> dict[str, object]:
    """The budget, and what IKARS has spent of it since start (tokens, waits, requests, 429s)."""
    return {
        **budget,
        "tokens_spent": round(bucket.stats.spent),
        "waits": bucket.stats.waits,
        "requests": client.stats.requests,
        "rate_limited": client.stats.rate_limited,
    }


def _at(tree: object, *keys: str) -> object:
    """``tree[k1][k2]...``, or None where a level is missing or not a mapping."""
    for key in keys:
        if not isinstance(tree, dict):
            return None
        tree = tree.get(key)
    return tree


def summary(health: dict[str, object]) -> str:
    """One line for the console: the numbers that say whether the feed is healthy."""

    def ms(percentile: str) -> float | None:
        ns = _at(health, "metrics", "timings", "engine.feed_to_route_ns", f"{percentile}_ns")
        return round(ns / 1e6, 1) if isinstance(ns, int) else None

    return json.dumps(
        {
            "frames": _at(health, "kalshi", "frames"),
            "watched": _at(health, "kalshi", "watched"),
            "books_ready": _at(health, "kalshi", "books_ready"),
            "open_gaps": _at(health, "kalshi", "open_gaps"),
            "feed_to_route_p50_ms": ms("p50"),
            "feed_to_route_p99_ms": ms("p99"),
            "rest_tokens_spent": _at(health, "request_budget", "tokens_spent"),
            "clock": health.get("clock"),
        },
        default=str,
    )


async def run(
    paths: Paths, settings: Settings, minutes: float | None, out: Callable[[str], None] = print
) -> dict[str, object]:
    paths.create()
    redactor = Redactor.from_environment(
        [settings.kalshi.key_id_variable, settings.kalshi.key_path_variable]
    )
    configure(
        paths.logs,
        redactor,
        level=settings.logs.level,
        max_bytes=settings.logs.max_bytes,
        files_kept=settings.logs.files_kept,
    )
    engine, source, state = await build(paths, settings)
    await source.start()
    running = asyncio.create_task(engine.run(), name="engine")
    try:
        elapsed = 0.0
        while minutes is None or elapsed < minutes * 60:
            await asyncio.sleep(min(60.0, minutes * 60 - elapsed) if minutes else 60.0)
            elapsed += 60.0 if minutes is None else min(60.0, minutes * 60 - elapsed)
            out(summary(engine.health()))
    finally:
        await source.stop()
        await running
        health = engine.health()
        state.close()
    return health


def default_paths(root: Path | None = None) -> Paths:
    from ikars_engine.paths import resolve

    return resolve() if root is None else Paths(root)
