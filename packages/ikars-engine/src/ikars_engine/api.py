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
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import msgspec
from ikars_core.book import OrderBook
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
HEALTH_EVERY_S = 5.0
PORTFOLIO_EVERY_S = 1.0
KEEPALIVE_S = 15.0
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
    streams: dict[str, "Stream"] = field(default_factory=dict)


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
    for text in complete.decode("utf-8", errors="replace").splitlines():
        state.alert_line += 1
        view = _alert(state.alert_line, text)
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


def read_alerts(path: Path, after: int | None, limit: int) -> list[c.AlertView]:
    """Alert-log lines numbered from 1: after ``after``, or the last ``limit`` when None."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    numbered = list(enumerate(lines, start=1))
    chosen = numbered[after:][:limit] if after is not None else numbered[-limit:]
    return [view for line, text in chosen if (view := _alert(line, text)) is not None]


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
        bodies = ctx.ledger.page(before, limit)
        last = bodies[-1]["seq"] if bodies else None
        older = last if last is not None and last > 1 and len(bodies) == limit else None
        return _json(c.LedgerPage(items=[record_view(b) for b in bodies],
                                  head_seq=ctx.ledger.head().seq, before=older))  # fmt: skip

    async def alerts(request: Request) -> Response:
        after = _int(request, "after", None, 0, 2**62)
        limit = _int(request, "limit", 100, 1, MAX_PAGE) or 100
        return _json(c.AlertPage(items=read_alerts(ctx.alert_log, after, limit)))

    async def recommendations(request: Request) -> Response:
        return _json(c.RecommendationPage(items=[]))  # from M1.12

    async def stream(request: Request) -> Response:
        after = _int(request, "after_alert", None, 0, 2**62)
        state = Stream(id=secrets.token_hex(8))
        _place(state, ctx.alert_log, after)
        ctx.streams[state.id] = state
        return StreamingResponse(_events(ctx, state, request), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store"})  # fmt: skip

    async def subscribe(request: Request) -> Response:
        state = ctx.streams.get(request.path_params["stream_id"])
        if state is None:
            return _error(404, "not_found", "no such stream (it may have closed)")
        try:
            wanted = msgspec.json.decode(await request.body(), type=c.Subscriptions)
        except msgspec.ValidationError as exc:
            return _error(400, "bad_request", str(exc))
        if len(wanted.quotes) > MAX_SUBSCRIPTIONS or len(wanted.books) > MAX_SUBSCRIPTIONS:
            return _error(400, "bad_request", f"at most {MAX_SUBSCRIPTIONS} markets each")
        state.quotes, state.books = set(wanted.quotes), set(wanted.books)
        return _json(wanted)

    routes = [
        Route("/v1/hello", hello), Route("/v1/health", health), Route("/v1/markets", markets),
        Route("/v1/markets/{ticker}", market), Route("/v1/books/{ticker}", book),
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


def _place(state: Stream, path: Path, after: int | None) -> None:
    """Put ``state`` at the end of the alert log (a new stream), or just after line ``after`` (a
    reconnecting one, which then receives the lines it missed)."""
    try:
        data = path.read_bytes()
    except OSError:
        return
    lines = data.count(b"\n")
    keep = lines if after is None else min(after, lines)
    offset = 0
    for _ in range(keep):
        offset = data.index(b"\n", offset) + 1
    state.alert_line, state.alert_offset = keep, offset


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


async def _events(ctx: ApiContext, state: Stream, request: Request) -> AsyncIterator[bytes]:
    loop = asyncio.get_running_loop()
    opened = c.StreamOpened(stream_id=state.id, coalesce_ms=int(ctx.coalesce_s * 1000))
    yield _event(state, "opened", opened)
    health_at = portfolio_at = keepalive_at = loop.time()
    try:
        while not await request.is_disconnected():
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
                health_at = keepalive_at = now
                yield _event(state, "health", c.HealthView(at_utc=now_utc(), parts=ctx.health()))
            elif now - keepalive_at >= KEEPALIVE_S:
                keepalive_at = now
                yield b": keep-alive\n\n"
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
            server_header=False, date_header=False,
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
