"""The loopback API (PLAN D3 as amended, M1.7): HTTP snapshots and one Server-Sent Events stream.

**Only this workstation, only its owner.** The server listens on ``127.0.0.1`` alone, on the port
in the settings (fixed, so an open terminal reconnects after a restart; a free one when it is
taken). The same origin serves the web terminal's files (``webserve``) and the API under ``/v1/``.
An API request carries either the per-start token (``Authorization: Bearer``, written with the port
to ``api.json`` in the data folder, the owner's alone; for the command line) or the terminal's
session cookie (``session``). A ``Host`` header that is not loopback is refused (DNS rebinding), and
so is any ``Origin`` but the engine's own; a request that changes anything on a cookie must come
from the engine's own page (``Origin`` and ``Sec-Fetch-Site: same-origin``). Read-only: nothing
here trades (PLAN D6).

**Snapshots** (``GET``): ``/v1/hello`` (the version handshake), ``/v1/health``, ``/v1/markets``,
``/v1/markets/{ticker}``, ``/v1/books/{ticker}``, ``/v1/portfolio``, ``/v1/ledger/status``,
``/v1/ledger/records``, ``/v1/alerts``, ``/v1/recommendations``.

**The stream** (``GET /v1/stream``): an ``opened`` event names the stream; ``PUT
/v1/stream/{id}/subscriptions`` sets which markets' quotes and books it carries. Every
``coalesce_ms`` the stream sends each subscribed market whose quote or book changed since it was
last sent (one event per market, however many deltas came in between: a market with 800 deltas a
second costs the terminal 10 events a second), the portfolio when it changed, new alert-log lines,
and health every few seconds. Each event has an ``id``; a reconnecting client passes
``?after_alert=<line>`` and the stream starts from the current state. The time from a frame's
arrival to its event leaving is measured (``api.feed_to_stream_ns``, PLAN D20 J).
"""

import asyncio
import hmac
import json
import logging
import os
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import msgspec
from ikars_core.book import OrderBook
from ikars_core.models import Candlestick
from ikars_core.winacl import other_readers
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from ikars_engine import contracts as c
from ikars_engine import runmode, support
from ikars_engine.files import write_text_atomic
from ikars_engine.kalshi.discovery import OPEN_STATUSES, MarketIndex, MarketInfo
from ikars_engine.ledger import KINDS, Ledger
from ikars_engine.metrics import Metrics
from ikars_engine.portfolio import Portfolio
from ikars_engine.runmode import at
from ikars_engine.session import COOKIE, Sessions
from ikars_engine.settings import Layout
from ikars_engine.settings_api import SettingsRefused, TerminalConfig
from ikars_engine.store import Gap
from ikars_engine.threads import in_daemon_thread
from ikars_engine.updates import CHECK_WAIT_S, Updater
from ikars_engine.webserve import SECURITY_HEADERS, files_route

log = logging.getLogger("ikars_engine.api")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
MAX_PAGE = 500
MAX_SUBSCRIPTIONS = 200
HEALTH_EVERY_S = 5.0  # also keeps an idle stream alive
PORTFOLIO_EVERY_S = 1.0
MAX_STREAMS = 8  # a terminal needs one or two
DROPPED = -1  # a stream's sent book version for a book the engine no longer keeps
MAX_CANDLE_HOURS = 24 * 7
CANDLE_CACHE_S = 60.0  # a chart reopened within a minute costs no request
MAX_CANDLE_CACHE = 256
SHUTDOWN_S = 3  # open streams end at stop; the server waits at most this long
NS = 1_000_000_000
SESSION_PATH = "/v1/session"
READS = frozenset({"GET", "HEAD"})
PORT_TRIES = 10  # the fixed port, tried this many times half a second apart (a restart's old
PORT_RETRY_S = 0.5  # socket may still hold it), before a free port is taken instead


def engine_version() -> str:
    try:
        return version("ikars-engine")
    except PackageNotFoundError:  # pragma: no cover - a source tree that was never installed
        return "unknown"


@dataclass
class ApiContext:
    """What the API reads, all owned by the running engine (the same event loop)."""

    token: str
    health: Callable[[], dict[str, Any]]
    index: MarketIndex
    quotes: Mapping[str, tuple[int | None, int | None]]
    last_price: Mapping[str, int]
    changed_ns: Mapping[str, int]  # a market's quote
    book_changed_ns: Mapping[str, int]
    books: Mapping[str, OrderBook]
    book_version: Mapping[str, int]
    watched: Callable[[], set[str]]
    ledger: Ledger
    ledger_health: Callable[[], dict[str, Any]]
    portfolio: Callable[[], Portfolio]
    alert_log: Path
    metrics: Metrics
    now_ns: Callable[[], int]
    coalesce_s: float = 0.1
    # Kalshi's candlesticks through the engine's request budget: (series, ticker, start_s, end_s,
    # interval minutes); None when the engine has no REST client (tests, offline)
    candles: "Callable[[str, str, int, int, int], Awaitable[list[Candlestick]]] | None" = None
    # the engine's gap log, ended at or after a time (newest first, at most a limit)
    gaps: "Callable[[int, int], list[Gap]] | None" = None
    streams: dict[str, "Stream"] = field(default_factory=dict)
    # (loop time, body, status): successes and failures alike, for CANDLE_CACHE_S
    candle_cache: dict[tuple[str, int, int], tuple[float, bytes, int]] = field(default_factory=dict)
    # the index in the markets list's order, and the index version it was sorted at (M1.R 16)
    sorted_index: tuple[int, list[MarketInfo]] = (-1, [])
    closing: bool = False  # set at stop: every stream ends
    alert_index: "AlertIndex | None" = None
    # the health view and when it was built: every stream shares one per interval (M1.8a-web-1a)
    health_cache: "tuple[float, c.HealthView] | None" = None
    port: int = 0  # set by the server once it listens: the engine's own origin
    sessions: Sessions | None = None  # the terminal's sign-in; None: the bearer token only
    web_root: Path | None = None  # the terminal's files; None: none served
    config: TerminalConfig | None = None  # the settings and their actions (M1.8b-1); None: none
    updates: "Updater | None" = None  # the program's updates (M1.9b); None: none


@dataclass
class Stream:
    id: str
    quotes: set[str] = field(default_factory=set)
    books: set[str] = field(default_factory=set)
    sent_quote: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    sent_book: dict[str, int] = field(default_factory=dict)
    sent_portfolio: bytes = b""
    alert_line: int = 0  # the last alert-log line sent
    alert_offset: int = 0  # where in the file that line ends: only new bytes are read
    events: int = 0


# --- the guard ----------------------------------------------------------------------------------


class Guard:
    """Before anything else runs: a loopback ``Host``; no ``Origin`` but the engine's own; the
    browser hardening headers on every response; and, for the API, the bearer token or a session
    cookie (a change on a cookie only from the engine's own page). Sets ``state["auth"]``."""

    def __init__(self, app: ASGIApp, ctx: ApiContext) -> None:
        self._app = app
        self._expected = f"Bearer {ctx.token}".encode()
        self._sessions = ctx.sessions
        self._own = {f"http://{host}:{ctx.port}".encode() for host in LOOPBACK_HOSTS}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":  # no route speaks it: refused, never let through (M1.R 14)
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":  # the server's own lifespan events
            await self._app(scope, receive, send)
            return
        api = scope["path"].startswith("/v1/")
        extra = [*SECURITY_HEADERS, (b"cache-control", b"no-store" if api else b"no-cache")]

        async def secured(message: Any) -> None:
            if message["type"] == "http.response.start":
                kept = [h for h in message.get("headers", []) if h[0].lower() != b"cache-control"]
                message = {**message, "headers": kept + extra}
            await send(message)

        headers = dict(scope["headers"])
        if host_of(headers.get(b"host", b"").decode("latin-1")) not in LOOPBACK_HOSTS:
            await _deny(secured, 403, "forbidden", "the engine answers loopback requests only")
            return
        origin = headers.get(b"origin")
        if origin is not None and origin not in self._own:
            await _deny(secured, 403, "forbidden", "the engine answers its own terminal only")
            return
        method = scope["method"]
        if not api:  # the terminal's files: public, read only
            if method not in READS:
                await _deny(secured, 405, "bad_request", "the terminal's files are read only")
                return
            await self._app(scope, receive, secured)
            return
        own_page = origin is not None and headers.get(b"sec-fetch-site") == b"same-origin"
        if hmac.compare_digest(headers.get(b"authorization", b""), self._expected):
            auth = "bearer"
        elif scope["path"] == SESSION_PATH and method == "POST":  # signing in: no session yet
            if not own_page:
                await _deny(secured, 403, "forbidden", "sign in from the terminal's own page")
                return
            auth = "signin"
        else:
            valid, renewed = self._cookie(headers.get(b"cookie", b""))
            if not valid:
                await _deny(secured, 401, "unauthorised", "sign in with: ikars open")
                return
            if method not in READS and not own_page:
                await _deny(secured, 403, "forbidden", "a change must come from the terminal")
                return
            if renewed is not None and self._sessions is not None:
                extra.append((b"set-cookie", self._sessions.set_cookie(renewed)))
            auth = "cookie"
        scope.setdefault("state", {})["auth"] = auth
        await self._app(scope, receive, secured)

    def _cookie(self, header: bytes) -> tuple[bool, str | None]:
        if self._sessions is None:
            return False, None
        for part in header.decode("latin-1").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return self._sessions.check(value)
        return False, None


def host_of(header: str) -> str:
    """The host in a ``Host`` header, without its port (``[::1]:80`` gives ``[::1]``)."""
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.rsplit(":", 1)[0]


async def _deny(
    send: Callable[[Any], Awaitable[None]], status: int, code: str, detail: str
) -> None:
    body = msgspec.json.encode(c.ApiError(error=code, detail=detail))
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]})  # fmt: skip
    await send({"type": "http.response.body", "body": body})


def _json(value: Any, status: int = 200) -> Response:
    return Response(msgspec.json.encode(value), status, media_type="application/json")


def _error(status: int, code: str, detail: str) -> Response:
    return _json(c.ApiError(error=code, detail=detail), status)


def _int(request: Request, name: str, default: int | None, low: int, high: int) -> int | None:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise _BadRequest(f"{name} must be a whole number") from None
    if not low <= value <= high:
        raise _BadRequest(f"{name} must be between {low} and {high}")
    return value


class _BadRequest(Exception):
    pass


class _NoConfig(Exception):
    """This engine has no settings to show (tests and the made-up-data server without them)."""


# --- views --------------------------------------------------------------------------------------


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def market_view(ctx: ApiContext, info: MarketInfo) -> c.MarketView:
    bid, ask = ctx.quotes.get(info.ticker, (None, None))
    close = info.close_time.isoformat().replace("+00:00", "Z") if info.close_time else None
    return c.MarketView(
        ticker=info.ticker, title=info.title, event_ticker=info.event_ticker,
        series_ticker=info.series_ticker, category=info.category, status=info.status,
        close_time_utc=close, volume_24h_e2=info.volume_24h_e2, yes_bid_e6=bid, yes_ask_e6=ask,
        last_price_e6=ctx.last_price.get(info.ticker), watched=info.ticker in ctx.watched(),
    )  # fmt: skip


def candle_view(row: Candlestick) -> c.Candle:
    return c.Candle(
        end_utc=row.end_period.isoformat().replace("+00:00", "Z"),
        open_e6=row.price.open_e6, high_e6=row.price.high_e6, low_e6=row.price.low_e6,
        close_e6=row.price.close_e6, bid_close_e6=row.yes_bid.close_e6,
        ask_close_e6=row.yes_ask.close_e6, volume_e2=row.volume_e2,
    )  # fmt: skip


def utc_text(ns: int) -> str:
    return datetime.fromtimestamp(ns // NS, UTC).isoformat().replace("+00:00", "Z")


def _typed[T](value: Any, kind: type[T]) -> T | None:
    """``value`` when it is a ``kind`` (never a bool for a number), else None: an odd value in
    the engine's report must never break the typed payload."""
    if isinstance(value, bool) and kind is not bool:
        return None
    if kind is float and isinstance(value, int):
        return float(value)  # type: ignore[return-value]
    return value if isinstance(value, kind) else None


def health_view(health: dict[str, Any]) -> c.HealthView:
    """The engine's health report as the terminal's typed view (PLAN D3; review 1.R2 item 2)."""
    plugins = at(health, "plugins")
    return c.HealthView(
        at_utc=now_utc(),
        feed=c.FeedHealth(
            connected=_typed(at(health, "kalshi", "feed", "connected"), bool),
            seconds_since_last_frame=_typed(
                at(health, "kalshi", "feed", "seconds_since_last_frame"), float
            ),
            open_gaps=_typed(at(health, "kalshi", "open_gaps"), int),
            books_ready=_typed(at(health, "kalshi", "books_ready"), int),
            watched_markets=_typed(at(health, "kalshi", "watched", "markets"), int),
            trading_active=_typed(at(health, "kalshi", "trading_active"), bool),
        ),
        storage_state=_typed(at(health, "kalshi", "storage", "state"), str),
        ledger=c.LedgerHealth(
            verified=_typed(at(health, "ledger", "verified"), bool),
            reconciled_exact=_typed(at(health, "ledger", "reconciled_exact"), bool),
            records=_typed(at(health, "ledger", "records"), int),
            errors=_typed(at(health, "ledger", "errors"), int),
        ),
        backup=c.BackupHealth(
            configured=_typed(at(health, "backup", "configured"), bool),
            hours_since=_typed(at(health, "backup", "hours_since"), int),
            last_error=_typed(at(health, "backup", "last_error"), str),
        ),
        clock_status=_typed(at(health, "clock", "status"), str),
        clock_offset_ms=_typed(at(health, "clock", "offset_ms"), float),
        gpu=_typed(at(health, "gpu", "device"), str),
        api_streams=_typed(at(health, "api", "streams"), int),
        setup_reason=_typed(at(health, "setup", "reason"), str),
        port_fallback=_typed(at(health, "api", "port_fallback"), bool),
        plugins={str(k): str(v) for k, v in plugins.items()} if isinstance(plugins, dict) else {},
        detail=health,
    )


def gap_view(gap: Gap) -> c.GapView:
    return c.GapView(
        id=gap.id, channel=gap.channel, reason=gap.reason, start_utc=utc_text(gap.start_ns),
        end_utc=utc_text(gap.end_ns), recovery=gap.recovery, status=gap.status, detail=gap.detail,
    )  # fmt: skip


def sorted_markets(ctx: "ApiContext") -> list[MarketInfo]:
    """The index, open markets first (closed ones keep their day's volume for a while), then by
    volume; sorted again only when the index changed, not on every request."""
    version, ordered = ctx.sorted_index
    if version != ctx.index.version:
        ordered = sorted(
            ctx.index.markets.values(),
            key=lambda m: (m.status not in OPEN_STATUSES, -m.volume_24h_e2, m.ticker),
        )
        ctx.sorted_index = (ctx.index.version, ordered)
    return ordered


def book_view(ticker: str, book: OrderBook) -> c.BookView:
    def side(name: str) -> list[c.Level]:
        return [c.Level(price_e6=p, count_e2=n) for p, n in book.bids(name)]

    return c.BookView(ticker=ticker, ready=book.ready, yes=side("yes"), no=side("no"))


def quote_update(ctx: ApiContext, ticker: str) -> c.QuoteUpdate:
    bid, ask = ctx.quotes.get(ticker, (None, None))
    return c.QuoteUpdate(ticker=ticker, yes_bid_e6=bid, yes_ask_e6=ask,
                         last_price_e6=ctx.last_price.get(ticker),
                         received_ns=ctx.changed_ns.get(ticker))  # fmt: skip


def portfolio_view(held: Portfolio) -> c.PortfolioView:
    return c.PortfolioView(
        cash_e6=held.cash_e6,
        cost_e6=held.cost_e6,
        unrealised_e6=held.unrealised_e6,
        positions=[
            c.PositionView(
                ticker=p.ticker,
                title=p.title,
                side=p.side,
                count_e2=p.count_e2,
                cost_e6=p.cost_e6,
                event_ticker=p.event_ticker,
                series_ticker=p.series_ticker,
                category=p.category,
                exit_price_e6=p.exit_price_e6,
                exit_fee_e6=p.exit_fee_e6,
                unrealised_e6=p.unrealised_e6,
            )
            for p in held.positions
        ],  # fmt: skip
        exposure_by_category=held.exposure("category"),
        exposure_by_event=held.exposure("event_ticker"),
    )


def record_view(body: dict[str, Any]) -> c.LedgerRecordView:
    return c.LedgerRecordView(
        seq=body["seq"], kind=body["kind"], time_utc=body["time_utc"],
        time_local=body["time_local"], source=body["source"], ticker=body.get("ticker"),
        cash_e6=body.get("cash_e6"), fields=body.get("fields") or {},
        context=body.get("context") or {}, note=body.get("note"),
    )  # fmt: skip


def new_alerts(path: Path, state: "Stream") -> list[c.AlertView]:
    """The alert-log lines added since ``state`` last looked (reads only the new bytes)."""
    try:
        with open(path, "rb") as handle:
            if handle.seek(0, 2) < state.alert_offset:  # the log was cut back: start it again
                state.alert_offset = state.alert_line = 0
            handle.seek(state.alert_offset)
            chunk = handle.read()
    except OSError:
        return []
    complete = chunk[: chunk.rfind(b"\n") + 1]  # a line still being written waits
    state.alert_offset += len(complete)
    views = []
    for raw in complete.split(b"\n")[:-1]:  # lines end at \n only, as the index counts them
        state.alert_line += 1
        view = _alert(state.alert_line, raw.decode("utf-8", errors="replace"))
        if view is not None:
            views.append(view)
    return views


def _alert(line: int, text: str) -> c.AlertView | None:
    try:
        entry = json.loads(text)
    except ValueError:
        return None
    return c.AlertView(
        line=line, key=str(entry.get("key", "")), priority=str(entry.get("priority", "")),
        title=str(entry.get("title", "")), text=str(entry.get("text", "")),
        at_local=str(entry.get("at", "")), outcome=str(entry.get("outcome", "")),
    )  # fmt: skip


class AlertIndex:
    """Where each line of the alert log starts, kept up to date by reading only what was added
    (the log only grows), so a page or a stream's starting point never reads the whole file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.starts: list[int] = []  # the byte offset of line n + 1
        self.end = 0  # where the last complete line ends

    def refresh(self) -> None:
        try:
            with open(self.path, "rb") as handle:
                if handle.seek(0, 2) < self.end:  # cut back: index it again
                    self.starts, self.end = [], 0
                handle.seek(self.end)
                chunk = handle.read()
        except OSError:
            return
        at = 0
        while (newline := chunk.find(b"\n", at)) != -1:
            self.starts.append(self.end + at)
            at = newline + 1
        self.end += at

    @property
    def lines(self) -> int:
        return len(self.starts)

    def offset_after(self, line: int) -> int:
        """Where line ``line + 1`` starts (the end of the log after its last line)."""
        return self.starts[line] if line < len(self.starts) else self.end

    def read(self, first: int, count: int) -> list[tuple[int, bytes]]:
        """Lines ``first`` .. ``first + count - 1`` (numbered from 1), with their numbers."""
        last = min(first + count - 1, len(self.starts))
        if first > last:
            return []
        with open(self.path, "rb") as handle:
            handle.seek(self.starts[first - 1])
            data = handle.read(self.offset_after(last) - self.starts[first - 1])
        return list(enumerate(data.split(b"\n")[: last - first + 1], start=first))


def read_alerts(index: AlertIndex, after: int | None, limit: int) -> list[c.AlertView]:
    """Alert-log lines numbered from 1: after ``after``, or the last ``limit`` when None."""
    index.refresh()
    first = after + 1 if after is not None else max(1, index.lines - limit + 1)
    try:
        chosen = index.read(first, limit)
    except OSError:
        return []
    texts = ((n, raw.decode("utf-8", errors="replace")) for n, raw in chosen)
    return [view for line, text in texts if (view := _alert(line, text)) is not None]


# --- the application ----------------------------------------------------------------------------


def create_app(ctx: ApiContext) -> ASGIApp:
    async def hello(request: Request) -> Response:
        return _json(c.Hello(api_version=c.API_VERSION, engine_version=engine_version(),
                             schema_sha256=c.schema_sha256()))  # fmt: skip

    async def health(request: Request) -> Response:
        return _json(health_view(ctx.health()))

    async def markets(request: Request) -> Response:
        query = request.query_params.get("q", "").strip().lower()
        category = request.query_params.get("category")
        offset = _int(request, "offset", 0, 0, 10**7) or 0
        limit = _int(request, "limit", 100, 1, MAX_PAGE) or 100
        found = [
            m for m in sorted_markets(ctx)
            if (not query or query in m.search) and (category is None or m.category == category)
        ]  # fmt: skip
        page = [market_view(ctx, m) for m in found[offset : offset + limit]]
        return _json(c.MarketPage(items=page, total=len(found), offset=offset))

    async def market(request: Request) -> Response:
        info = ctx.index.markets.get(request.path_params["ticker"])
        if info is None:
            return _error(404, "not_found", "no such market in the index")
        return _json(market_view(ctx, info))

    async def book(request: Request) -> Response:
        ticker = request.path_params["ticker"]
        kept = ctx.books.get(ticker)
        if kept is None:
            return _error(404, "not_found", "the engine keeps no book for this market")
        return _json(book_view(ticker, kept))

    async def portfolio(request: Request) -> Response:
        return _json(portfolio_view(ctx.portfolio()))

    async def ledger_status(request: Request) -> Response:
        part = ctx.ledger_health()
        return _json(c.LedgerStatusView(
            records={k: n for k in sorted(KINDS) if (n := ctx.ledger.count(kind=k))},
            head_seq=ctx.ledger.head().seq, verified=part.get("verified"),
            reconciled_exact=part.get("reconciled_exact"), tax_zone=ctx.ledger.zone.key,
        ))  # fmt: skip

    async def ledger_records(request: Request) -> Response:
        before = _int(request, "before", None, 1, 2**62)
        limit = _int(request, "limit", 100, 1, MAX_PAGE) or 100
        ticker = request.query_params.get("ticker") or None
        bodies = ctx.ledger.page(before, limit, ticker=ticker)
        last = bodies[-1]["seq"] if bodies else None
        older = last if last is not None and last > 1 and len(bodies) == limit else None
        return _json(c.LedgerPage(items=[record_view(b) for b in bodies],
                                  head_seq=ctx.ledger.head().seq, before=older))  # fmt: skip

    async def alerts(request: Request) -> Response:
        after = _int(request, "after", None, 0, 2**62)
        limit = _int(request, "limit", 100, 1, MAX_PAGE) or 100
        return _json(c.AlertPage(items=read_alerts(alert_index(ctx), after, limit)))

    async def candles(request: Request) -> Response:
        ticker = request.path_params["ticker"]
        interval = _int(request, "interval", 1, 1, 60) or 1
        if interval not in (1, 60):
            raise _BadRequest("interval must be 1 or 60 (minutes)")
        hours = _int(request, "hours", 24, 1, MAX_CANDLE_HOURS) or 24
        info = ctx.index.markets.get(ticker)
        if info is None or ctx.candles is None:
            return _error(404, "not_found", "no such market in the index, or no price history")
        key = (ticker, interval, hours)
        now = asyncio.get_running_loop().time()
        cached = ctx.candle_cache.get(key)
        if cached is not None and now - cached[0] < CANDLE_CACHE_S:
            return Response(cached[1], status_code=cached[2], media_type="application/json")
        end_s = ctx.now_ns() // NS
        try:
            rows = await ctx.candles(
                info.series_ticker, ticker, end_s - hours * 3600, end_s, interval
            )
        except Exception as exc:  # Kalshi refused or failed: say so, never a bare 500
            log.warning("candles not read", extra={"error": type(exc).__name__})
            failed = _error(503, "unavailable", "Kalshi's price history could not be read now")
            remember(key, now, bytes(failed.body), 503)  # a failure is not asked again for a minute
            return failed
        page = c.CandlePage(
            ticker=ticker, interval_minutes=interval, items=[candle_view(r) for r in rows]
        )
        body = msgspec.json.encode(page)
        remember(key, now, body, 200)
        return Response(body, media_type="application/json")

    def remember(key: tuple[str, int, int], now: float, body: bytes, status: int) -> None:
        if len(ctx.candle_cache) >= MAX_CANDLE_CACHE:
            ctx.candle_cache.clear()
        ctx.candle_cache[key] = (now, body, status)

    async def gaps(request: Request) -> Response:
        days = _int(request, "days", 7, 1, 90) or 7
        limit = _int(request, "limit", 200, 1, MAX_PAGE) or 200
        found = [] if ctx.gaps is None else ctx.gaps(ctx.now_ns() - days * 86_400 * NS, limit)
        return _json(c.GapPage(items=[gap_view(g) for g in found]))

    async def recommendations(request: Request) -> Response:
        return _json(c.RecommendationPage(items=[]))  # from M1.12

    async def stream(request: Request) -> Response:
        after = _int(request, "after_alert", None, 0, 2**62)
        if len(ctx.streams) >= MAX_STREAMS:
            return _error(503, "unavailable", f"at most {MAX_STREAMS} streams at once")
        state = Stream(id=secrets.token_hex(8))
        _place(state, alert_index(ctx), after)
        return StreamingResponse(_events(ctx, state, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})  # fmt: skip

    async def signin(request: Request) -> Response:
        if ctx.sessions is None:
            return _error(404, "not_found", "this engine has no terminal sign-in")
        try:
            wanted = msgspec.json.decode(await request.body(), type=c.SessionCode)
        except msgspec.DecodeError as exc:
            return _error(400, "bad_request", str(exc))
        value = ctx.sessions.redeem(wanted.code)
        if value is None:
            return _error(401, "unauthorised", "the code was used, expired or unknown")
        cookie = ctx.sessions.set_cookie(value).decode("ascii")
        return Response(status_code=204, headers={"set-cookie": cookie})

    async def session_code(request: Request) -> Response:
        if ctx.sessions is None:
            return _error(404, "not_found", "this engine has no terminal sign-in")
        if request.state.auth != "bearer":  # only the command line opens a terminal
            return _error(403, "forbidden", "codes are issued to the command line only")
        code = ctx.sessions.issue_code()
        url = f"http://127.0.0.1:{ctx.port}/#code={code}"
        return _json(c.SessionCodeIssued(code=code, url=url))

    def configured() -> TerminalConfig:
        if ctx.config is None:
            raise _NoConfig
        return ctx.config

    async def settings_view(request: Request) -> Response:
        try:  # a thread: the view queries the task scheduler and reads files (merge review 1)
            return _json(await in_daemon_thread(configured().view))
        except SettingsRefused as exc:  # the file cannot be read: said, never a 500
            return _error(503, "unavailable", str(exc))

    async def settings_change(request: Request) -> Response:
        config = configured()
        try:
            wanted = msgspec.json.decode(await request.body(), type=c.SettingsChange)
            return _json(await in_daemon_thread(config.change, wanted))
        except msgspec.DecodeError as exc:
            return _error(400, "bad_request", str(exc))
        except SettingsRefused as exc:
            return _error(400, "bad_request", str(exc))

    async def share(request: Request) -> Response:
        config = configured()
        try:
            wanted = msgspec.json.decode(await request.body(), type=c.ShareCheck)
        except msgspec.DecodeError as exc:
            return _error(400, "bad_request", str(exc))
        try:
            if request.url.path.endswith("init"):
                return _json(await config.share_init(wanted.share, ctx.now_ns()))
            return _json(await config.share_check(wanted.share))
        except TimeoutError:
            return _error(503, "unavailable", "the share did not answer in time")

    async def autostart_install(request: Request) -> Response:
        return _json(await in_daemon_thread(configured().install_autostart))

    async def restart(request: Request) -> Response:
        return _json(configured().restart())

    async def support_bundle(request: Request) -> Response:
        """A redacted support bundle in the data folder's support folder (never sent anywhere)."""
        config = configured()
        held = ctx.portfolio()
        unrealised = held.unrealised_e6 or 0
        amounts = [held.cash_e6, held.cost_e6, held.cash_e6 + held.cost_e6 + unrealised]
        for position in held.positions:
            amounts += [position.cost_e6, position.cost_e6 + (position.unrealised_e6 or 0)]
        health = msgspec.json.encode(shared_health(ctx, asyncio.get_running_loop().time()))

        def make() -> c.Done:
            return support.make(config.paths, config.running, dict(os.environ), token=ctx.token,
                                health=health, amounts_e6=amounts)  # fmt: skip

        try:
            return _json(await in_daemon_thread(make))
        except (support.BundleRefused, OSError) as exc:
            return _json(c.Done(done=False, detail=str(exc)))

    def updater() -> "Updater":
        return ctx.updates if ctx.updates is not None else Updater()  # none: "not installed"

    async def update_view(request: Request) -> Response:
        return _json(updater().view())

    async def update_check(request: Request) -> Response:
        try:
            return _json(await asyncio.wait_for(in_daemon_thread(updater().check), CHECK_WAIT_S))
        except TimeoutError:
            return _error(503, "unavailable", "the update check did not finish in time")

    async def update_apply(request: Request) -> Response:
        """Restart to update: Velopack installs the downloaded release once this engine stops."""
        config = configured()
        if not config.restartable:  # nothing would start it again (as for a restart)
            return _json(c.Done(done=False, detail="this engine runs in the foreground: stop it "
                                "and start IKARS from the Start menu to update"))  # fmt: skip
        return _json(updater().apply(lambda: runmode.request_stop(config.paths.data)))

    async def layouts(request: Request) -> Response:
        try:
            return _json(await in_daemon_thread(configured().layouts))
        except SettingsRefused as exc:
            return _error(503, "unavailable", str(exc))

    async def layout_change(request: Request) -> Response:
        """Save a layout, choose the one restored at start, or delete one (M1.8b-2)."""
        config = configured()
        try:
            if request.method == "DELETE":
                name = request.path_params["name"]
                return _json(await in_daemon_thread(config.delete_layout, name))
            if request.url.path.endswith("/start"):
                start = msgspec.json.decode(await request.body(), type=c.LayoutStart)
                return _json(await in_daemon_thread(config.start_layout, start.name))
            layout = msgspec.json.decode(await request.body(), type=Layout)
            return _json(await in_daemon_thread(config.save_layout, layout))
        except (msgspec.DecodeError, SettingsRefused) as exc:
            return _error(400, "bad_request", str(exc))

    async def subscribe(request: Request) -> Response:
        state = ctx.streams.get(request.path_params["stream_id"])
        if state is None:
            return _error(404, "not_found", "no such stream (it may have closed)")
        try:
            wanted = msgspec.json.decode(await request.body(), type=c.Subscriptions)
        except msgspec.DecodeError as exc:  # malformed JSON or the wrong shape
            return _error(400, "bad_request", str(exc))
        if len(wanted.quotes) > MAX_SUBSCRIPTIONS or len(wanted.books) > MAX_SUBSCRIPTIONS:
            return _error(400, "bad_request", f"at most {MAX_SUBSCRIPTIONS} markets each")
        state.quotes, state.books = set(wanted.quotes), set(wanted.books)
        # a market dropped and added back starts afresh: its current state is sent again
        state.sent_quote = {t: v for t, v in state.sent_quote.items() if t in state.quotes}
        state.sent_book = {t: v for t, v in state.sent_book.items() if t in state.books}
        return _json(wanted)

    routes = [
        Route("/v1/hello", hello), Route("/v1/health", health), Route("/v1/markets", markets),
        Route("/v1/markets/{ticker}", market), Route("/v1/books/{ticker}", book),
        Route("/v1/markets/{ticker}/candles", candles),
        Route("/v1/portfolio", portfolio), Route("/v1/ledger/status", ledger_status),
        Route("/v1/ledger/records", ledger_records), Route("/v1/alerts", alerts),
        Route("/v1/recommendations", recommendations), Route("/v1/gaps", gaps),
        Route("/v1/stream", stream),
        Route("/v1/stream/{stream_id}/subscriptions", subscribe, methods=["PUT"]),
        Route(SESSION_PATH, signin, methods=["POST"]),
        Route("/v1/session/code", session_code, methods=["POST"]),
        Route("/v1/settings", settings_view),
        Route("/v1/settings", settings_change, methods=["PUT"]),
        Route("/v1/settings/share-check", share, methods=["POST"]),
        Route("/v1/settings/share-init", share, methods=["POST"]),
        Route("/v1/engine/autostart", autostart_install, methods=["POST"]),
        Route("/v1/engine/restart", restart, methods=["POST"]),
        Route("/v1/support/bundle", support_bundle, methods=["POST"]),
        Route("/v1/update", update_view),
        Route("/v1/update/check", update_check, methods=["POST"]),
        Route("/v1/update/apply", update_apply, methods=["POST"]),
        Route("/v1/layouts", layouts), Route("/v1/layouts", layout_change, methods=["PUT"]),
        Route("/v1/layouts/start", layout_change, methods=["PUT"]),
        Route("/v1/layouts/{name}", layout_change, methods=["DELETE"]),
    ]  # fmt: skip
    if ctx.web_root is not None:  # last: every other path is the terminal's
        routes.append(Route("/{path:path}", files_route(ctx.web_root), methods=["GET", "HEAD"]))

    async def bad_request(request: Request, exc: Exception) -> Response:
        return _error(400, "bad_request", str(exc))

    async def not_found(request: Request, exc: Exception) -> Response:
        return _error(404, "not_found", "no such route")

    async def wrong_method(request: Request, exc: Exception) -> Response:
        return _error(405, "bad_request", "this route does not take that method")

    async def no_config(request: Request, exc: Exception) -> Response:
        return _error(404, "not_found", "this engine has no settings to show")

    handlers = {_BadRequest: bad_request, _NoConfig: no_config, 404: not_found, 405: wrong_method}
    app = Starlette(routes=routes, exception_handlers=handlers)
    return Guard(app, ctx)


# --- the stream ---------------------------------------------------------------------------------


def alert_index(ctx: ApiContext) -> AlertIndex:
    if ctx.alert_index is None:
        ctx.alert_index = AlertIndex(ctx.alert_log)
    return ctx.alert_index


def _place(state: Stream, index: AlertIndex, after: int | None) -> None:
    """Put ``state`` at the end of the alert log (a new stream), or just after line ``after`` (a
    reconnecting one, which then receives the lines it missed)."""
    index.refresh()
    keep = index.lines if after is None else min(after, index.lines)
    state.alert_line, state.alert_offset = keep, index.offset_after(keep)


def _event(state: Stream, name: str, payload: Any) -> bytes:
    state.events += 1
    data = msgspec.json.encode(payload).decode("utf-8")
    return f"id: {state.events}\nevent: {name}\ndata: {data}\n\n".encode()


def changes(ctx: ApiContext, state: Stream) -> list[bytes]:
    """The quote and book events due for ``state`` now (only what changed since last sent).

    Latency is measured on updates only: the first event for a market after it is subscribed is
    its current state, as old as the market is quiet, not a measure of the stream."""
    out: list[bytes] = []
    now = ctx.now_ns()
    for ticker in sorted(state.quotes):
        update = quote_update(ctx, ticker)
        key = (update.yes_bid_e6, update.yes_ask_e6, update.last_price_e6)
        if state.sent_quote.get(ticker) != key and update.received_ns is not None:
            if ticker in state.sent_quote:
                ctx.metrics.observe("api.feed_to_stream_ns", max(0, now - update.received_ns))
            state.sent_quote[ticker] = key
            out.append(_event(state, "quote", update))
    for ticker in sorted(state.books):
        seen = ctx.book_version.get(ticker)
        kept = ctx.books.get(ticker)
        if kept is None:
            # dropped (a reconnect rebuilds every book; a market left the watched set): say so once,
            # so a screen never keeps showing the old book as ready (M1.R 10)
            if state.sent_book.get(ticker, DROPPED) != DROPPED:
                state.sent_book[ticker] = DROPPED
                out.append(
                    _event(state, "book", c.BookView(ticker=ticker, ready=False, yes=[], no=[]))
                )
            continue
        if seen is not None and state.sent_book.get(ticker) != seen:
            received = ctx.book_changed_ns.get(ticker)
            if ticker in state.sent_book and received is not None:
                ctx.metrics.observe("api.feed_to_stream_ns", max(0, now - received))
            state.sent_book[ticker] = seen
            out.append(_event(state, "book", book_view(ticker, kept)))
    return out


def shared_health(ctx: ApiContext, now: float) -> c.HealthView:
    """The health view, built at most once per half interval however many streams are open (the
    report reads SQLite and the metrics; M1.8a-web-1a merge review)."""
    if ctx.health_cache is None or now - ctx.health_cache[0] >= HEALTH_EVERY_S / 2:
        ctx.health_cache = (now, health_view(ctx.health()))
    return ctx.health_cache[1]


async def _events(ctx: ApiContext, state: Stream, request: Request) -> AsyncGenerator[bytes]:
    loop = asyncio.get_running_loop()
    opened = c.StreamOpened(stream_id=state.id, coalesce_ms=int(ctx.coalesce_s * 1000))
    if len(ctx.streams) >= MAX_STREAMS:  # several arrived at once: the cap holds here too
        busy = c.ApiError(error="unavailable", detail=f"at most {MAX_STREAMS} streams at once")
        yield _event(state, "error", busy)
        return
    ctx.streams[state.id] = state  # registered only once it runs, so it is always removed
    health_at = portfolio_at = loop.time()
    try:
        yield _event(state, "opened", opened)
        while not ctx.closing and not await request.is_disconnected():
            for chunk in changes(ctx, state):
                yield chunk
            now = loop.time()
            if now - portfolio_at >= PORTFOLIO_EVERY_S:
                portfolio_at = now
                view = portfolio_view(ctx.portfolio())
                encoded = msgspec.json.encode(view)
                if encoded != state.sent_portfolio:
                    state.sent_portfolio = encoded
                    yield _event(state, "portfolio", view)
                for alert in new_alerts(ctx.alert_log, state):
                    yield _event(state, "alert", alert)
            if now - health_at >= HEALTH_EVERY_S:
                health_at = now
                yield _event(state, "health", shared_health(ctx, now))
            await asyncio.sleep(ctx.coalesce_s)
    finally:
        ctx.streams.pop(state.id, None)


# --- the server ---------------------------------------------------------------------------------

API_FILE = "api.json"


class ApiServer:
    """uvicorn inside the engine's event loop, on the settings' loopback port (0: any free port;
    a free one too when the fixed port stays taken, reported in health). The port and the per-start
    token go to ``api.json`` (the owner's alone), removed at stop."""

    def __init__(self, ctx_for: Callable[[str], ApiContext], folder: Path, port: int = 0) -> None:
        self._ctx_for = ctx_for
        self._file = folder / API_FILE
        self._wanted = port
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None
        self.port: int | None = None
        self.port_fallback = False  # the fixed port was taken: an open terminal cannot reconnect
        self.ctx: ApiContext | None = None

    async def _listen(self) -> Any:
        import socket

        tries = PORT_TRIES if self._wanted else 1
        for attempt in range(tries):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: nobody can bind it beside us
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                listener.bind(("127.0.0.1", self._wanted))
                return listener
            except OSError:
                listener.close()
                if attempt + 1 < tries:
                    await asyncio.sleep(PORT_RETRY_S)
        self.port_fallback = True
        log.warning(
            "the terminal's port is taken: using a free one", extra={"wanted": self._wanted}
        )
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(("127.0.0.1", 0))
        return listener

    async def start(self) -> None:
        import uvicorn

        listener = await self._listen()
        self.port = listener.getsockname()[1]
        token = secrets.token_urlsafe(32)
        self.ctx = self._ctx_for(token)
        self.ctx.port = self.port
        config = uvicorn.Config(
            create_app(self.ctx), log_level="warning", access_log=False, lifespan="off",
            server_header=False, date_header=False, timeout_graceful_shutdown=SHUTDOWN_S,
            # uvicorn must not configure logging: its console formatter fails under pythonw (no
            # console: live finding), and the engine's redacting log handlers already cover it
            log_config=None,
        )  # fmt: skip
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # the engine owns its signals
        self._task = asyncio.create_task(self._server.serve(sockets=[listener]), name="api")
        while not self._server.started:  # api.json only once a client can connect
            if self._task.done():
                self._task.result()  # raises what stopped it
                raise RuntimeError("the API server stopped before it started")
            await asyncio.sleep(0.01)
        record = {"port": self.port, "token": token, "api_version": c.API_VERSION}
        write_text_atomic(self._file, json.dumps(record))
        try:
            readers = other_readers(self._file)
        except (OSError, NotImplementedError):  # off Windows there is no ACL to read (M1.R 26)
            readers = []
        if readers:  # the token would be readable by someone else: say so (never the token)
            log.warning("api.json is readable by other accounts", extra={"readers": len(readers)})

    async def stop(self) -> None:
        if self.ctx is not None:
            self.ctx.closing = True  # open streams end, so the server can
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
        self._file.unlink(missing_ok=True)

    def health(self) -> dict[str, object]:
        streams = 0 if self.ctx is None else len(self.ctx.streams)
        sessions = None if self.ctx is None else self.ctx.sessions
        return {
            "port": self.port,
            "port_fallback": self.port_fallback,
            "streams": streams,
            "api_version": c.API_VERSION,
            "sessions_kept": None if sessions is None else sessions.persistent,
        }


def read_api_file(folder: Path) -> dict[str, Any] | None:
    """The running engine's port and token (for a client on this machine), or None."""
    try:
        data = json.loads((folder / API_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
