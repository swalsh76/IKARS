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
import itertools
import json
import logging
import os
import sqlite3
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import msgspec
from ikars_core.account import Account
from ikars_core.auth import AccountSigner, AuthError, DataSigner
from ikars_core.clock import ClockCheckError, sample_from_response
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData
from ikars_core.fees import FeeSchedule
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import (
    KalshiAccountClient,
    KalshiAPIError,
    KalshiConnectionError,
    KalshiPublicClient,
)
from ikars_core.ws import FeedConfig, KalshiFeed

from ikars_engine import gpu, taxzone
from ikars_engine.alerts import ALERT_LOG
from ikars_engine.api import ApiContext, ApiServer, market_fees, market_title
from ikars_engine.backup_service import BackupService
from ikars_engine.clocks import WallClock
from ikars_engine.engine import Engine
from ikars_engine.governor import GB, StorageGovernor
from ikars_engine.journal import Journal, JournalService
from ikars_engine.kalshi.discovery import OPEN_STATUSES, Discovery, MarketIndex
from ikars_engine.kalshi.exchange import ExchangeWatch
from ikars_engine.kalshi.source import KalshiSource
from ikars_engine.kalshi.watched import WatchedSet
from ikars_engine.ledger import Ledger
from ikars_engine.ledger_import import AccountImporter, Describe
from ikars_engine.ledger_service import LedgerService
from ikars_engine.logs import Redactor, configure
from ikars_engine.metrics import Metrics
from ikars_engine.model import Item
from ikars_engine.paths import Paths
from ikars_engine.plugins import HostOptions, PluginError, discover
from ikars_engine.portfolio import Held, Portfolio, holdings, portfolio
from ikars_engine.recommendations import ENGINE_DESK, Notices
from ikars_engine.recorder import Recorder
from ikars_engine.runmode import at
from ikars_engine.runmode_cli import desk_for
from ikars_engine.session import Sessions
from ikars_engine.settings import Settings
from ikars_engine.settings_api import TerminalConfig
from ikars_engine.share import configured, migration_copier
from ikars_engine.skew import SkewMonitor
from ikars_engine.store import StateStore
from ikars_engine.threads import in_daemon_thread
from ikars_engine.updates import Updater
from ikars_engine.webserve import terminal_root

log = logging.getLogger("ikars_engine.kalshi.runner")
SAMPLE_EVERY_S = 60.0
TICK_S = 2.0  # how often the run loop looks for a stop request
MINUTE_S = 60.0
FINISH_S = 10.0  # how long the engine may take to drain at shutdown before it is cancelled


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


NETWORK_WAITS_S = (5, 10, 20, 40, 60)  # then a minute each time


class StoppedWaiting(Exception):
    """The owner stopped the engine while it waited for the network."""


async def first_bucket(
    config: KalshiConfig,
    share_percent: int,
    signer: AccountSigner,
    should_stop: Callable[[], bool] | None = None,
    on_wait: Callable[[int | None], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[TokenBucket, dict[str, object]]:
    """:func:`request_bucket`, waiting for the network: an engine started while it is down, or
    while Kalshi answers 5xx (a reboot during an outage), keeps trying instead of stopping (owner,
    2026-10-28). ``on_wait`` gets the seconds waited after each failed try, and None once Kalshi
    answers after a wait; a stop request ends a try in flight too; a key fault still stops it."""
    began = monotonic()
    for attempt in itertools.count():
        try:
            bucket = await _until_stopped(
                request_bucket(config, share_percent, signer), should_stop
            )
            if attempt and on_wait is not None:
                on_wait(None)  # reachable again: no stale "waiting" for the watchdog (review 4)
            return bucket
        except (KalshiConnectionError, KalshiAPIError) as exc:
            if isinstance(exc, KalshiAPIError) and exc.status < 500:
                raise  # Kalshi answered and refused: not the network (review 3)
            waited = int(monotonic() - began)
            log.warning(
                "waiting for the network",
                extra={"error": type(exc.__cause__ or exc).__name__, "waited_s": waited},
            )
            if on_wait is not None:
                on_wait(waited)
            end = monotonic() + NETWORK_WAITS_S[min(attempt, len(NETWORK_WAITS_S) - 1)]
            while (left := end - monotonic()) > 0:
                if should_stop is not None and should_stop():
                    raise StoppedWaiting from exc
                await sleep(min(TICK_S, left))
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


async def _until_stopped[T](work: Awaitable[T], should_stop: Callable[[], bool] | None) -> T:
    """``work``, cancelled when a stop is asked for while it runs (a try against a network that
    drops packets can take minutes, longer than ``ikars stop`` waits: review 2)."""
    task = asyncio.ensure_future(work)
    while True:
        done, _ = await asyncio.wait({task}, timeout=TICK_S)
        if done:
            return task.result()
        if should_stop is not None and should_stop():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise StoppedWaiting


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
    gpu_task: asyncio.Task[None]
    api: ApiServer
    public_client: KalshiPublicClient
    updates: Updater
    journal: Journal
    journal_service: JournalService
    recorder: Recorder


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


async def build(
    paths: Paths,
    settings: Settings,
    root: Path | None = None,
    restartable: bool = False,
    should_stop: Callable[[], bool] | None = None,
    on_wait: Callable[[int | None], None] | None = None,
) -> Live:
    config = KalshiConfig(environment=Environment(settings.kalshi.environment))
    zone = taxzone.resolve(settings.tax_time_zone)  # a bad setting fails before anything opens
    clock = WallClock()
    metrics = Metrics()
    skew = SkewMonitor()
    account_signer = AccountSigner.from_environment(signer_environment(settings))
    bucket, budget = await first_bucket(
        config, settings.feed.read_share_percent, account_signer, should_stop, on_wait
    )
    log.info("request budget", extra=budget)
    client = KalshiPublicClient(config, bucket=bucket, observe=clock_observer(skew))
    data = MarketData(client)
    state = StateStore(paths.data / "state.sqlite")
    signer = DataSigner.from_environment(signer_environment(settings))
    feed = KalshiFeed(signer, FeedConfig(url=config.ws_url))
    watched = WatchedSet(
        max_markets=settings.feed.watched_max_markets,
        max_deltas_per_second=settings.feed.watched_max_deltas_per_second,
        pinned=set(settings.feed.watch),
    )
    index = MarketIndex()
    recorder = Recorder(paths.recordings)
    source = KalshiSource(
        feed=feed,
        data=data,
        discovery=Discovery(data, index, state, clock),
        exchange=ExchangeWatch(data, state, clock),
        state=state,
        recorder=recorder,
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
    journal = Journal(paths.data / "journal.sqlite")
    plugin_defaults = settings.plugins
    options = HostOptions(
        queue_size=plugin_defaults.queue_size, call_timeout=plugin_defaults.call_timeout_seconds,
        restart_base=plugin_defaults.restart_base_seconds,
        restart_max=plugin_defaults.restart_max_seconds, settings=settings.plugin_settings,
        services={"market": index.markets.get, "fees": index.fees.get, "active": journal.active},
    )  # fmt: skip
    engine = Engine(clock, source, metrics=metrics, skew=skew, host_options=options)
    # the plugins (review 1.R2 item 8): one that cannot load is said in health, never a stop
    plugins, unloaded = discover()
    for plugin in plugins:
        try:
            engine.host.add(plugin)
        except PluginError as exc:  # two of one name (merge review 2): the second is left out
            unloaded.append(f"{plugin.spec.name}: {exc}")

    def result(ticker: str) -> str | None:  # a market's settlement, once the index has it
        found = index.markets.get(ticker)
        return None if found is None else found.result

    def trading(ticker: str) -> bool | None:  # whether the market still trades, by the index
        found = index.markets.get(ticker)
        return None if found is None else found.status in OPEN_STATUSES

    # the strongest recommendations notify (M1.12c), through the engine's own alert desk
    notices = Notices(desk_for(paths, settings, ENGINE_DESK), market_title(index))
    journal_service = JournalService(
        journal, source.quotes, clock.now_ns, result=result, trading=trading,
        fees=market_fees(index), notices=notices,
    )  # fmt: skip
    engine.listeners += [journal_service.listen, recording(recorder)]
    engine.health_parts["journal"] = journal_service.health
    engine.health_parts["plugins_unloaded"] = lambda: {"problems": list(unloaded)}
    cards: dict[str, object] = {"device": None, "note": "detecting"}

    async def detect_gpu() -> None:  # in the background: a hung driver never delays the start
        try:
            found = gpu.health(await in_daemon_thread(gpu.detect))
        except Exception as exc:  # never "detecting" for ever: say it failed
            log.warning("GPU detection failed", extra={"error": type(exc).__name__})
            found = {"device": None, "note": "detection failed"}
        cards.clear()
        cards.update(found)

    engine.health_parts["gpu"] = lambda: dict(cards)
    gpu_task = asyncio.create_task(detect_gpu(), name="gpu")
    engine.health_parts["kalshi"] = source.health
    engine.health_parts["ledger"] = service.health
    backups = BackupService(share, paths, state, clock)
    engine.health_parts["backup"] = backups.health
    held = PortfolioHealth(ledger, source.quotes, index.fees.get, index.categories.get)
    engine.health_parts["portfolio"] = held

    sessions = Sessions(paths.data, settings.terminal.session_days)
    web_root = terminal_root()
    updates = Updater.for_this_program()

    def api_context(token: str) -> ApiContext:
        return ApiContext(
            token=token,
            health=engine.health,
            index=index,
            quotes=source.quotes,
            last_price=source.last_price_e6,
            changed_ns=source.changed_ns,
            book_changed_ns=source.book_changed_ns,
            books=source.books,
            book_version=source.book_version,
            watched=lambda: source.watched.current,
            ledger=ledger,
            ledger_health=service.health,
            portfolio=held.portfolio,
            alert_log=paths.data / ALERT_LOG,
            metrics=metrics,
            now_ns=clock.now_ns,
            gaps=state.gaps_since,
            backup=backups.request,
            candles=lambda series, ticker, start, end, interval: data.candlesticks(
                series, ticker, start_ts=start, end_ts=end, period_interval=interval
            ),
            sessions=sessions,
            web_root=web_root,
            config=TerminalConfig(paths, settings, root, restartable=restartable),
            updates=updates,
            journal=journal,
            recommendations=journal_service,
        )

    api = ApiServer(api_context, paths.data, port=settings.terminal.port)
    engine.health_parts["api"] = api.health
    engine.health_parts["request_budget"] = lambda: requests_used(budget, bucket, client)
    return Live(
        engine, source, state, ledger, service, backups, account_client, gpu_task, api, client,
        updates, journal, journal_service, recorder,
    )  # fmt: skip


class PortfolioHealth:
    """The portfolio, for health (as counts) and the API (in full). The lots are rebuilt only
    when the ledger's head moves, so neither replays the whole ledger on the event loop; pricing
    them from the live quotes is cheap."""

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

    def portfolio(self) -> Portfolio:
        seq = self._ledger.head().seq
        if self._held is None or seq != self._seq:
            self._held, self._seq = holdings(self._ledger), seq
        return portfolio(self._quotes, self._fees, self._categories, self._held)

    def __call__(self) -> dict[str, object]:
        held = self.portfolio()
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


def summary(health: dict[str, object]) -> str:
    """One line for the console: the numbers that say whether the feed is healthy."""

    def ms(percentile: str) -> float | None:
        ns = at(health, "metrics", "timings", "engine.feed_to_route_ns", f"{percentile}_ns")
        return round(ns / 1e6, 1) if isinstance(ns, int) else None

    return json.dumps(
        {
            "frames": at(health, "kalshi", "frames"),
            "watched": at(health, "kalshi", "watched"),
            "books_ready": at(health, "kalshi", "books_ready"),
            "open_gaps": at(health, "kalshi", "open_gaps"),
            "feed_to_route_p50_ms": ms("p50"),
            "feed_to_route_p99_ms": ms("p99"),
            "rest_tokens_spent": at(health, "request_budget", "tokens_spent"),
            "ledger_records": at(health, "ledger", "records"),
            "ledger_exact": at(health, "ledger", "reconciled_exact"),
            "clock": health.get("clock"),
        },
        default=str,
    )


async def run(
    paths: Paths,
    settings: Settings,
    minutes: float | None,
    out: Callable[[str], None] = print,
    should_stop: Callable[[], bool] | None = None,
    on_minute: Callable[[dict[str, object]], None] | None = None,
    root: Path | None = None,
    on_wait: Callable[[int | None], None] | None = None,
) -> dict[str, object]:
    """The live engine until ``minutes`` pass or ``should_stop`` says so (checked every two
    seconds); each minute, a summary line to ``out`` and the health to ``on_minute`` (and once at
    once, so a watchdog sees a fresh snapshot from the start)."""
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
    problem = key_problem(settings)
    if problem is not None:
        if has_history(paths.ledger / "ledger.sqlite"):
            # an install with records: a key that fails is a fault to fix, not a first run; the
            # engine stops, so the watchdog restarts it and raises an urgent alert (merge review 2)
            raise AuthError(problem)
        # a fresh install: the terminal only, to set IKARS up (M1.9a)
        log.warning("setup mode: the Kalshi key cannot be used", extra={"problem": problem})
        return await run_setup(paths, settings, problem, should_stop, on_minute, root, minutes)
    began = time.monotonic()

    def stop_waiting() -> bool:  # `run --minutes N` ends a wait for the network too (review 6)
        asked = should_stop is not None and should_stop()
        return asked or (minutes is not None and time.monotonic() - began >= minutes * 60)

    try:
        live = await build(
            paths,
            settings,
            root,
            should_stop is not None,
            should_stop=stop_waiting,
            on_wait=on_wait,
        )
    except StoppedWaiting:
        log.info("stopped while waiting for the network")
        return {"stopped": "while waiting for the network"}
    engine = live.engine
    health: dict[str, object] = {}
    running: list[asyncio.Task[object]] = []
    # every step of the shutdown runs even when one before it fails, in the reverse of this order
    async with contextlib.AsyncExitStack() as stack:
        stack.push_async_callback(live.account_client.aclose)
        stack.push_async_callback(live.public_client.aclose)  # M1.R 24
        stack.callback(live.state.close)
        # last of all, once the plugins have drained (merge review 4 and 5): what they emitted
        # while stopping is recorded (the source closed the recorder; closing again flushes it)
        # and written to the journal before it closes, after the API that reads it has stopped
        stack.callback(live.recorder.close)
        stack.callback(live.journal.close)
        if (
            live.journal_service.notices is not None
        ):  # after the flush, which may offer one (review 1)
            stack.callback(live.journal_service.notices.close)
        stack.callback(live.journal_service.flush)
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
        stack.push_async_callback(live.api.stop)  # stops before the ledger it reads closes
        await live.api.start()
        running.append(asyncio.create_task(engine.run(), name="engine"))
        checking = asyncio.create_task(live.updates.keep_checking(), name="updates")
        stack.callback(checking.cancel)  # it sleeps a day at a time: cancelled, not awaited
        writing = asyncio.create_task(live.journal_service.run(), name="journal")
        stack.callback(writing.cancel)
        if on_minute is not None:
            on_minute(engine.health())
        started = last = time.monotonic()
        while True:
            await asyncio.sleep(TICK_S)
            if running[0].done():  # the engine ended by itself: say so, so serve exits and the
                raise EngineStopped(_ended(running[0]))  # watchdog sees it and restarts it
            if (lost := live.source.failed()) is not None:  # a dead pump is no engine (M1.R 6)
                raise EngineStopped(f"the Kalshi source stopped: {lost}")
            now = time.monotonic()
            if now - last >= MINUTE_S:
                last = now
                current = engine.health()
                out(summary(current))
                if on_minute is not None:
                    on_minute(current)
            if (should_stop is not None and should_stop()) or (
                minutes is not None and now - started >= minutes * 60
            ):
                if last != now:  # a last summary, unless this tick just printed one
                    out(summary(engine.health()))
                break
    return health


def signer_environment(
    settings: Settings, environ: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The key's variables as the signers read them, from the names the settings give (merge
    review 4: the wizard and Settings read those names too)."""
    env = os.environ if environ is None else environ
    return {
        "IKARS_KALSHI_KEY_ID": env.get(settings.kalshi.key_id_variable, ""),
        "IKARS_KALSHI_PRIVATE_KEY_PATH": env.get(settings.kalshi.key_path_variable, ""),
    }


def recording(recorder: Recorder) -> Callable[[Item], None]:
    """The engine's listener that records every item a plugin emits (D20 A; review 1.R2 item 8):
    a row in the recorder's batch, never a wait."""

    def record(item: Item) -> None:
        recorder.write("item", {
            "received_ns": item.received_ns, "published_ns": item.published_ns,
            "kind": item.ref.kind, "id": item.ref.id, "source": item.source, "hops": item.hops,
            "payload": msgspec.json.encode(item.payload).decode(),
        })  # fmt: skip

    return record


def key_problem(settings: Settings, environ: Mapping[str, str] | None = None) -> str | None:
    """Why IKARS's Kalshi key cannot be used, in words (never its id or path), or None."""
    try:
        AccountSigner.from_environment(signer_environment(settings, environ))
    except AuthError as exc:
        return str(exc)
    return None


def has_history(ledger: Path) -> bool:
    """Whether the real ledger holds any record (read only): an install that has run in full."""
    if not ledger.is_file():
        return False
    try:
        with contextlib.closing(sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True)) as db:
            return db.execute("SELECT 1 FROM record LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


async def run_setup(
    paths: Paths,
    settings: Settings,
    problem: str,
    should_stop: Callable[[], bool] | None = None,
    on_minute: Callable[[dict[str, object]], None] | None = None,
    root: Path | None = None,
    minutes: float | None = None,
) -> dict[str, object]:
    """Setup mode (M1.9a; owner, 2026-10-27): the key cannot be used (a fresh machine has none),
    so no feed, no ledger import and no call to Kalshi; only the API and the terminal, so the
    first-run wizard and Settings can set IKARS up. Health says why; the watchdog tells the owner
    and does not restart it; once the key is set, Settings' Restart the engine starts it in full."""
    zone = taxzone.resolve(settings.tax_time_zone)
    clock = WallClock()
    # what the ledger holds, for the terminal; a migration copies it to the share first, as always
    ledger = Ledger(
        paths.ledger / "ledger.sqlite",
        zone.zone,
        migration_copier(configured(settings.backup.share)),
    )
    held = PortfolioHealth(ledger, {}, lambda ticker: None, lambda ticker: None)
    sessions = Sessions(paths.data, settings.terminal.session_days)
    restartable = should_stop is not None
    servers: list[ApiServer] = []
    updates = Updater.for_this_program()  # a machine being set up gets updates too

    def health() -> dict[str, object]:
        report: dict[str, object] = {
            "setup": {"reason": problem, "next": "set the key, then restart the engine"},
            "kalshi": {"feed": {"connected": False}},
        }
        if servers:
            report["api"] = servers[0].health()
        return report

    def api_context(token: str) -> ApiContext:
        return ApiContext(
            token=token, health=health, index=MarketIndex(), quotes={}, last_price={},
            changed_ns={}, book_changed_ns={}, books={}, book_version={}, watched=set,
            ledger=ledger, ledger_health=dict, portfolio=held.portfolio,
            alert_log=paths.data / ALERT_LOG, metrics=Metrics(), now_ns=clock.now_ns,
            sessions=sessions, web_root=terminal_root(),
            config=TerminalConfig(paths, settings, root, restartable=restartable), updates=updates,
        )  # fmt: skip

    api = ApiServer(api_context, paths.data, port=settings.terminal.port)
    servers.append(api)
    checking = asyncio.create_task(updates.keep_checking(), name="updates")
    try:
        await api.start()
        if on_minute is not None:
            on_minute(health())
        started = last = time.monotonic()
        while True:
            await asyncio.sleep(TICK_S)
            now = time.monotonic()
            if now - last >= MINUTE_S:
                last = now
                if on_minute is not None:
                    on_minute(health())
            if (should_stop is not None and should_stop()) or (
                minutes is not None and now - started >= minutes * 60
            ):
                break
    finally:
        checking.cancel()
        await api.stop()
        ledger.close()
    return health()


class EngineStopped(RuntimeError):
    """The engine's main task ended while nobody asked it to."""


def _ended(task: asyncio.Task[object]) -> str:
    if task.cancelled():
        return "the engine's main task was cancelled"
    error = task.exception()
    return "the engine's main task ended" + ("" if error is None else f": {type(error).__name__}")


async def _finish(tasks: list[asyncio.Task[object]]) -> None:
    """Wait for the engine to drain, but never for ever: a shutdown always finishes (M1.R 4)."""
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=FINISH_S)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def default_paths(root: Path | None = None) -> Paths:
    from ikars_engine.paths import resolve

    return resolve() if root is None else Paths(root)
