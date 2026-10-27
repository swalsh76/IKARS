"""The loopback API (PLAN D3 as amended, M1.7): HTTP snapshots and one Server-Sent Events stream.

**Only this workstation, only its owner.** The server listens on ``127.0.0.1`` alone. Every request
must carry the session's token (``Authorization: Bearer``), made fresh at each start and written
with the port to ``api.json`` in the data folder (the owner's alone; never logged). A ``Host``
header that is not loopback is refused (DNS rebinding), and so is any browser ``Origin`` (no CORS).
Read-only: nothing here trades (PLAN D6).

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
from ikars_engine.files import write_text_atomic
from ikars_engine.kalshi.discovery import MarketIndex, MarketInfo
from ikars_engine.ledger import KINDS, Ledger
from ikars_engine.metrics import Metrics
from ikars_engine.portfolio import Portfolio

log = logging.getLogger("ikars_engine.api")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
MAX_PAGE = 500
MAX_SUBSCRIPTIONS = 200
HEALTH_EVERY_S = 5.0  # also keeps an idle stream alive
PORTFOLIO_EVERY_S = 1.0
MAX_STREAMS = 8  # a terminal needs one or two
MAX_CANDLE_HOURS = 24 * 7
CANDLE_CACHE_S = 60.0  # a chart reopened within a minute costs no request
MAX_CANDLE_CACHE = 256
SHUTDOWN_S = 3  # open streams end at stop; the server waits at most this long
NS = 1_000_000_000


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
    streams: dict[str, "Stream"] = field(default_factory=dict)
    candle_cache: dict[tuple[str, int, int], tuple[float, bytes]] = field(default_factory=dict)
    closing: bool = False  # set at stop: every stream ends
    alert_index: "AlertIndex | None" = None


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
    """Token, loopback ``Host`` and no browser ``Origin``, before anything else runs."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        if host_of(headers.get(b"host", b"").decode("latin-1")) not in LOOPBACK_HOSTS:
            await _deny(send, 403, "forbidden", "the API answers loopback requests only")
            return
        if b"origin" in headers:
            await _deny(send, 403, "forbidden", "the API does not answer browsers")
            return
        if not hmac.compare_digest(headers.get(b"authorization", b""), self._expected):
            await _deny(send, 401, "unauthorised", "a valid session token is required")
            return
        await self._app(scope, receive, send)


def host_of(header: str) -> str:
    """The host in a ``Host`` header, without its port (``[::1]:80`` gives ``[::1]``)."""
    value = header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else value
    return value.rsplit(":", 1)[0]


async def _deny(send: Send, status: int, code: str, detail: str) -> None:
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
        at=str(entry.get("at", "")), outcome=str(entry.get("outcome", "")),
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
        return _json(c.HealthView(at_utc=now_utc(), parts=ctx.health()))

    async def markets(request: Request) -> Response:
        query = request.query_params.get("q", "").strip().lower()
        category = request.query_params.get("category")
        offset = _int(request, "offset", 0, 0, 10**7) or 0
        limit = _int(request, "limit", 100, 1, MAX_PAGE) or 100
        found = [
            m for m in ctx.index.markets.values()
            if (not query or query in m.ticker.lower() or query in m.title.lower())
            and (category is None or m.category == category)
        ]  # fmt: skip
        found.sort(key=lambda m: (-m.volume_24h_e2, m.ticker))
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
            return Response(cached[1], media_type="application/json")
        end_s = ctx.now_ns() // NS
        try:
            rows = await ctx.candles(
                info.series_ticker, ticker, end_s - hours * 3600, end_s, interval
            )
        except Exception as exc:  # Kalshi refused or failed: say so, never a bare 500
            log.warning("candles not read", extra={"error": type(exc).__name__})
            return _error(503, "unavailable", "Kalshi's price history could not be read now")
        page = c.CandlePage(
            ticker=ticker, interval_minutes=interval, items=[candle_view(r) for r in rows]
        )
        body = msgspec.json.encode(page)
        if len(ctx.candle_cache) >= MAX_CANDLE_CACHE:
            ctx.candle_cache.clear()
        ctx.candle_cache[key] = (now, body)
        return Response(body, media_type="application/json")

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
        Route("/v1/recommendations", recommendations), Route("/v1/stream", stream),
        Route("/v1/stream/{stream_id}/subscriptions", subscribe, methods=["PUT"]),
    ]  # fmt: skip

    async def bad_request(request: Request, exc: Exception) -> Response:
        return _error(400, "bad_request", str(exc))

    async def not_found(request: Request, exc: Exception) -> Response:
        return _error(404, "not_found", "no such route")

    app = Starlette(routes=routes, exception_handlers={_BadRequest: bad_request, 404: not_found})
    return Guard(app, ctx.token)


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
        if seen is not None and kept is not None and state.sent_book.get(ticker) != seen:
            received = ctx.book_changed_ns.get(ticker)
            if ticker in state.sent_book and received is not None:
                ctx.metrics.observe("api.feed_to_stream_ns", max(0, now - received))
            state.sent_book[ticker] = seen
            out.append(_event(state, "book", book_view(ticker, kept)))
    return out


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
                yield _event(state, "health", c.HealthView(at_utc=now_utc(), parts=ctx.health()))
            await asyncio.sleep(ctx.coalesce_s)
    finally:
        ctx.streams.pop(state.id, None)


# --- the server ---------------------------------------------------------------------------------

API_FILE = "api.json"


class ApiServer:
    """uvicorn inside the engine's event loop, on a free loopback port chosen at each start; the
    port and the session's token go to ``api.json`` (the owner's alone), removed at stop."""

    def __init__(self, ctx_for: Callable[[str], ApiContext], folder: Path) -> None:
        self._ctx_for = ctx_for
        self._file = folder / API_FILE
        self._server: Any = None
        self._task: asyncio.Task[None] | None = None
        self.port: int | None = None
        self.ctx: ApiContext | None = None

    async def start(self) -> None:
        import socket

        import uvicorn

        token = secrets.token_urlsafe(32)
        self.ctx = self._ctx_for(token)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        self.port = listener.getsockname()[1]
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
        except OSError:
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
        return {"port": self.port, "streams": streams, "api_version": c.API_VERSION}


def read_api_file(folder: Path) -> dict[str, Any] | None:
    """The running engine's port and token (for a client on this machine), or None."""
    try:
        data = json.loads((folder / API_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
