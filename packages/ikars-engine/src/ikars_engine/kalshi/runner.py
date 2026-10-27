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
import contextlib
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import msgspec
from ikars_core.account import Account
from ikars_core.auth import AccountSigner, DataSigner
from ikars_core.clock import ClockCheckError, sample_from_response
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import KalshiAccountClient, KalshiPublicClient
from ikars_core.ws import FeedConfig, KalshiFeed

from ikars_engine import taxzone
from ikars_engine.backup_service import BackupService
from ikars_engine.clocks import WallClock
from ikars_engine.engine import Engine
from ikars_engine.governor import GB, StorageGovernor
from ikars_engine.kalshi.discovery import Discovery, MarketIndex
from ikars_engine.kalshi.exchange import ExchangeWatch
from ikars_engine.kalshi.source import KalshiSource
from ikars_engine.kalshi.watched import WatchedSet
from ikars_engine.ledger import Ledger
from ikars_engine.ledger_import import AccountImporter, Describe
from ikars_engine.ledger_service import LedgerService
from ikars_engine.logs import Redactor, configure
from ikars_engine.metrics import Metrics
from ikars_engine.paths import Paths
from ikars_engine.portfolio import Held, holdings, portfolio
from ikars_engine.recorder import Recorder
from ikars_engine.settings import Settings
from ikars_engine.share import configured, migration_copier
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
    config: KalshiConfig, share_percent: int, signer: AccountSigner
) -> tuple[TokenBucket, dict[str, object]]:
    """A token bucket of ``share_percent`` of the account's read bucket (read once at start)."""
    async with KalshiAccountClient(config, signer) as client:
        limits = await Account(client).limits()
    rate = limits.read.refill_rate * share_percent / 100
    burst = limits.read.bucket_capacity * share_percent / 100
    return TokenBucket(rate=rate, capacity=max(burst, config.request_cost_tokens)), {
        "tier": limits.usage_tier,
        "tokens_per_s": rate,
        "burst_tokens": burst,
    }


@dataclass(slots=True)
class Live:
    """Everything ``run`` starts and stops."""

    engine: Engine
    source: KalshiSource
    state: StateStore
    ledger: Ledger
    ledger_service: LedgerService
    backup: BackupService
    account_client: KalshiAccountClient


def describer(index: MarketIndex) -> Describe:
    """A market's title, event, series and category from the discovery index, for the ledger."""

    def describe(ticker: str) -> dict[str, str] | None:
        info = index.markets.get(ticker)
        if info is None:
            return None
        return {"title": info.title, "event_ticker": info.event_ticker,
                "series_ticker": info.series_ticker, "category": info.category}  # fmt: skip

    return describe


def settings_digest(settings: Settings) -> str:
    return hashlib.sha256(msgspec.json.encode(settings)).hexdigest()


async def build(paths: Paths, settings: Settings) -> Live:
    config = KalshiConfig(environment=Environment(settings.kalshi.environment))
    zone = taxzone.resolve(settings.tax_time_zone)  # a bad setting fails before anything opens
    clock = WallClock()
    metrics = Metrics()
    skew = SkewMonitor()
    account_signer = AccountSigner.from_environment()
    bucket, budget = await request_bucket(config, settings.feed.read_share_percent, account_signer)
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
    index = MarketIndex()
    source = KalshiSource(
        feed=feed,
        data=data,
        discovery=Discovery(data, index, state, clock),
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
    share = configured(settings.backup.share)
    ledger = Ledger(paths.ledger / "ledger.sqlite", zone.zone, migration_copier(share))
    account_client = KalshiAccountClient(config, account_signer, bucket=bucket)  # opened last
    service = LedgerService(
        ledger,
        AccountImporter(ledger, Account(account_client), clock, describer(index), data),
        paths.ledger / "checkpoints",
        clock,
        zone,
        key_fingerprint=account_signer.fingerprint,
        settings_sha256=settings_digest(settings),
    )
    engine = Engine(clock, source, metrics=metrics, skew=skew)
    engine.health_parts["kalshi"] = source.health
    engine.health_parts["ledger"] = service.health
    backups = BackupService(share, paths, state, clock)
    engine.health_parts["backup"] = backups.health
    engine.health_parts["portfolio"] = PortfolioHealth(
        ledger, source.quotes, index.fees.get, index.categories.get
    )
    engine.health_parts["request_budget"] = lambda: requests_used(budget, bucket, client)
    return Live(engine, source, state, ledger, service, backups, account_client)


class PortfolioHealth:
    """The portfolio in health, as counts (its values go to the terminal, M1.7). The lots are
    rebuilt only when the ledger's head moves, so a health call never replays the whole ledger
    on the event loop; pricing them from the live quotes is cheap."""

    def __init__(
        self,
        ledger: Ledger,
        quotes: Mapping[str, tuple[int | None, int | None]],
        fees: Callable[[str], FeeSchedule | None],
        categories: Callable[[str], str | None],
    ) -> None:
        self._ledger = ledger
        self._quotes = quotes
        self._fees = fees
        self._categories = categories
        self._seq = -1
        self._held: Held | None = None

    def __call__(self) -> dict[str, object]:
        seq = self._ledger.head().seq
        if self._held is None or seq != self._seq:
            self._held, self._seq = holdings(self._ledger), seq
        held = portfolio(self._quotes, self._fees, self._categories, self._held)
        priced = sum(1 for p in held.positions if p.unrealised_e6 is not None)
        return {"positions": len(held.positions), "priced": priced}


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
            "ledger_records": _at(health, "ledger", "records"),
            "ledger_exact": _at(health, "ledger", "reconciled_exact"),
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
    live = await build(paths, settings)
    engine = live.engine
    health: dict[str, object] = {}
    running: list[asyncio.Task[object]] = []
    # every step of the shutdown runs even when one before it fails, in the reverse of this order
    async with contextlib.AsyncExitStack() as stack:
        stack.push_async_callback(live.account_client.aclose)
        stack.callback(live.state.close)
        stack.callback(live.ledger.close)
        stack.callback(lambda: health.update(engine.health()))
        stack.push_async_callback(_finish, running)
        live.ledger_service.check()  # before the feed: a long walk never delays a frame
        stack.push_async_callback(live.source.stop)
        await live.source.start()
        stack.push_async_callback(live.ledger_service.stop)
        await live.ledger_service.start()  # after discovery, so records get market titles
        stack.push_async_callback(live.backup.stop)
        await live.backup.start()
        running.append(asyncio.create_task(engine.run(), name="engine"))
        elapsed = 0.0
        while minutes is None or elapsed < minutes * 60:
            await asyncio.sleep(min(60.0, minutes * 60 - elapsed) if minutes else 60.0)
            elapsed += 60.0 if minutes is None else min(60.0, minutes * 60 - elapsed)
            out(summary(engine.health()))
    return health


async def _finish(tasks: list[asyncio.Task[object]]) -> None:
    await asyncio.gather(*tasks, return_exceptions=True)


def default_paths(root: Path | None = None) -> Paths:
    from ikars_engine.paths import resolve

    return resolve() if root is None else Paths(root)
