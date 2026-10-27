"""M1.7: the loopback API (the guard, the snapshots, the stream) and its contracts. Every market,
book, ledger record and alert here is made up."""

import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import msgspec
import pytest
from ikars_core.book import OrderBook
from ikars_core.ws_models import parse_frame
from ikars_engine import contracts as c
from ikars_engine.api import (
    AlertIndex,
    ApiContext,
    ApiServer,
    Stream,
    _events,
    _place,
    changes,
    create_app,
    new_alerts,
    read_alerts,
    read_api_file,
)
from ikars_engine.kalshi.discovery import MarketIndex, MarketInfo
from ikars_engine.ledger import Entry, Ledger
from ikars_engine.metrics import Metrics
from ikars_engine.portfolio import Held, portfolio

REPO = Path(__file__).parents[3]
TOKEN = "t" * 43
T0 = 1_767_225_600 * 10**9
AUTH = {"Authorization": f"Bearer {TOKEN}"}


# --- the contracts ------------------------------------------------------------------------------


def test_the_committed_contracts_match_the_code(tmp_path: Path) -> None:
    """Drift between contracts.py and contracts/ fails here (re-run: contracts export)."""
    c.export(tmp_path)
    committed = REPO / "contracts"
    for made in sorted(tmp_path.rglob("*.json")):
        mine = committed / made.relative_to(tmp_path)
        assert mine.read_text(encoding="utf-8") == made.read_text(encoding="utf-8"), mine.name
    assert len(list(committed.rglob("*.json"))) == len(list(tmp_path.rglob("*.json")))


def test_every_example_decodes_as_its_contract() -> None:
    by_name = {kind.__name__: kind for kind in c.CONTRACTS}
    for name, value in c.examples().items():
        again = msgspec.json.decode(msgspec.json.encode(value), type=by_name[name])
        assert again == value
    assert set(c.examples()) == set(by_name) and len(c.schema_sha256()) == 64


# --- a made-up engine ---------------------------------------------------------------------------


def info(ticker: str, volume: int, category: str = "Demo") -> MarketInfo:
    return MarketInfo(ticker=ticker, event_ticker="KXDEMO-26JAN01", series_ticker="KXDEMO",
                      category=category, title=f"Made up {ticker}", status="active",
                      close_time=None, result=None, volume_24h_e2=volume)  # fmt: skip


def snapshot_book(ticker: str) -> OrderBook:
    raw = {"type": "orderbook_snapshot", "sid": 1, "seq": 1,
           "msg": {"market_ticker": ticker, "yes_dollars_fp": [["0.4100", "12.50"]],
                   "no_dollars_fp": [["0.5600", "3.00"]]}}  # fmt: skip
    book = OrderBook(ticker)
    book.apply_snapshot(parse_frame(json.dumps(raw)).payload)  # type: ignore[arg-type]
    return book


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[ApiContext]:
    index = MarketIndex()
    for ticker, volume in (("KXA", 500), ("KXB", 900), ("OTHER", 100)):
        index.markets[ticker] = info(ticker, volume, "Other" if ticker == "OTHER" else "Demo")
    ledger = Ledger(tmp_path / "ledger.sqlite", ZoneInfo("America/Denver"))
    for n in range(3):
        ledger.append(Entry("deposit", T0 + n, "deposit", f"d{n}", cash_e6=1_000_000))
    alerts = tmp_path / "alerts.jsonl"
    lines = [
        json.dumps({"key": f"k{n}", "priority": "high", "title": "t", "text": f"alert {n}",
                    "at": "x", "outcome": "shown"})
        for n in range(1, 4)
    ]  # fmt: skip
    alerts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    now = [T0 + 10**9]
    context = ApiContext(
        token=TOKEN,
        health=lambda: {"kalshi": {"open_gaps": 0}},
        index=index,
        quotes={"KXA": (410_000, 430_000)},
        last_price={"KXA": 420_000},
        changed_ns={"KXA": T0},
        book_changed_ns={"KXA": T0},
        books={"KXA": snapshot_book("KXA")},
        book_version={"KXA": 1},
        watched=lambda: {"KXA"},
        ledger=ledger,
        ledger_health=lambda: {"verified": True, "reconciled_exact": True},
        portfolio=lambda: portfolio({}, lambda s: None, lambda s: None, Held({}, 3_000_000)),
        alert_log=alerts,
        metrics=Metrics(),
        now_ns=lambda: now[0],
        coalesce_s=0.0,
    )
    yield context
    ledger.close()


async def call(
    ctx: ApiContext,
    method: str,
    path: str,
    *,
    headers: dict[str, str] = AUTH,
    body: Any = None,
    base: str = "http://127.0.0.1",
) -> httpx.Response:
    """The app in-process, on this test's event loop (as uvicorn runs it in the engine's)."""
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url=base) as http:
        return await http.request(method, path, headers=headers, json=body)


# --- the guard ----------------------------------------------------------------------------------


async def test_the_guard_wants_the_token_loopback_and_no_browser(ctx: ApiContext) -> None:
    assert (await call(ctx, "GET", "/v1/hello", headers={})).status_code == 401
    wrong = {"Authorization": "Bearer wrong"}
    assert (await call(ctx, "GET", "/v1/hello", headers=wrong)).status_code == 401
    refused = await call(ctx, "GET", "/v1/hello", base="http://evil.example")
    assert refused.status_code == 403 and refused.json()["error"] == "forbidden"
    browser = {**AUTH, "Origin": "http://127.0.0.1"}
    assert (await call(ctx, "GET", "/v1/hello", headers=browser)).status_code == 403
    assert (await call(ctx, "GET", "/v1/hello")).status_code == 200


# --- the snapshots ------------------------------------------------------------------------------


async def get(ctx: ApiContext, path: str, kind: type[Any], status: int = 200) -> Any:
    response = await call(ctx, "GET", path)
    assert response.status_code == status, response.text
    return msgspec.json.decode(response.content, type=kind)


async def test_the_gap_log_by_days(ctx: ApiContext, tmp_path: Path) -> None:
    from ikars_engine.store import StateStore

    state = StateStore(tmp_path / "state.sqlite")
    old = state.add_gap(
        "trade", "restart", T0 - 30 * 86_400 * 10**9, T0 - 29 * 86_400 * 10**9, "backfill", T0
    )
    new = state.add_gap("book", "reconnect", T0 - 10**9, T0, "unrecoverable", T0, "down 1.0 s")
    ctx.gaps = state.gaps_since
    page = await get(ctx, "/v1/gaps?days=7", c.GapPage)
    assert [g.id for g in page.items] == [new] and old != new
    assert page.items[0].status == "unrecoverable" and page.items[0].end_utc.endswith("Z")
    assert len((await get(ctx, "/v1/gaps?days=90", c.GapPage)).items) == 2
    ctx.gaps = None
    assert (await get(ctx, "/v1/gaps", c.GapPage)).items == []
    state.close()


async def test_hello_health_and_recommendations(ctx: ApiContext) -> None:
    hello = await get(ctx, "/v1/hello", c.Hello)
    assert hello.api_version == c.API_VERSION and hello.schema_sha256 == c.schema_sha256()
    assert (await get(ctx, "/v1/health", c.HealthView)).parts == {"kalshi": {"open_gaps": 0}}
    assert (await get(ctx, "/v1/recommendations", c.RecommendationPage)).items == []


async def test_markets_search_page_and_quote(ctx: ApiContext) -> None:
    page = await get(ctx, "/v1/markets?category=Demo", c.MarketPage)
    assert [m.ticker for m in page.items] == ["KXB", "KXA"] and page.total == 2  # by volume
    found = await get(ctx, "/v1/markets?q=other", c.MarketPage)
    assert [m.ticker for m in found.items] == ["OTHER"]
    second = await get(ctx, "/v1/markets?limit=1&offset=1", c.MarketPage)
    assert [m.ticker for m in second.items] == ["KXA"] and second.offset == 1
    ctx.index.markets["KXDONE"] = replace(info("KXDONE", 9_999), status="closed")
    ctx.index.version += 1  # as put() does
    closed_last = await get(ctx, "/v1/markets?category=Demo", c.MarketPage)
    assert [m.ticker for m in closed_last.items] == ["KXB", "KXA", "KXDONE"]  # open ones first
    one = await get(ctx, "/v1/markets/KXA", c.MarketView)
    quote = (one.yes_bid_e6, one.yes_ask_e6, one.last_price_e6, one.watched)
    assert quote == (410_000, 430_000, 420_000, True)
    assert (await get(ctx, "/v1/markets/NOPE", c.ApiError, 404)).error == "not_found"
    assert (await get(ctx, "/v1/markets?limit=9999", c.ApiError, 400)).error == "bad_request"
    assert (await get(ctx, "/v1/markets?offset=x", c.ApiError, 400)).error == "bad_request"


async def test_books_portfolio_and_unknown_routes(ctx: ApiContext) -> None:
    book = await get(ctx, "/v1/books/KXA", c.BookView)
    assert book.ready and book.yes == [c.Level(price_e6=410_000, count_e2=1_250)]
    assert (await get(ctx, "/v1/books/KXB", c.ApiError, 404)).error == "not_found"
    held = await get(ctx, "/v1/portfolio", c.PortfolioView)
    assert held.cash_e6 == 3_000_000 and held.positions == []
    assert (await get(ctx, "/v1/nowhere", c.ApiError, 404)).error == "not_found"


async def test_ledger_status_and_pages(ctx: ApiContext) -> None:
    status = await get(ctx, "/v1/ledger/status", c.LedgerStatusView)
    assert (status.records, status.head_seq, status.verified) == ({"deposit": 3}, 3, True)
    first = await get(ctx, "/v1/ledger/records?limit=2", c.LedgerPage)
    assert [r.seq for r in first.items] == [3, 2] and first.before == 2
    rest = await get(ctx, f"/v1/ledger/records?limit=2&before={first.before}", c.LedgerPage)
    assert [r.seq for r in rest.items] == [1] and rest.before is None
    assert rest.items[0].cash_e6 == 1_000_000 and rest.items[0].kind == "deposit"
    ctx.ledger.append(Entry("fill", T0 + 9, "fill", "f9", ticker="KXA", cash_e6=-400_000))
    one = await get(ctx, "/v1/ledger/records?ticker=KXA", c.LedgerPage)
    assert [(r.seq, r.ticker) for r in one.items] == [(4, "KXA")]  # one market's records


async def test_the_alert_log_by_line(ctx: ApiContext) -> None:
    last_two = await get(ctx, "/v1/alerts?limit=2", c.AlertPage)
    assert [a.line for a in last_two.items] == [2, 3]
    after = await get(ctx, "/v1/alerts?after=1", c.AlertPage)
    assert [a.text for a in after.items] == ["alert 2", "alert 3"]
    assert read_alerts(AlertIndex(ctx.alert_log.with_name("none.jsonl")), None, 5) == []


# --- the stream ---------------------------------------------------------------------------------


def test_a_stream_sends_only_what_changed(ctx: ApiContext) -> None:
    state = Stream(id="s", quotes={"KXA", "KXB"}, books={"KXA", "KXB"})
    first = changes(ctx, state)
    names = [chunk.split(b"\n")[1] for chunk in first]
    assert names == [b"event: quote", b"event: book"]  # KXB: no quote yet, no book kept
    assert changes(ctx, state) == []  # nothing changed since
    ctx.quotes["KXA"] = (415_000, 430_000)  # type: ignore[index]
    ctx.book_version["KXA"] = 2  # type: ignore[index]
    assert len(changes(ctx, state)) == 2
    timings = ctx.metrics.snapshot()["timings"]
    # the first send of each is a snapshot, not an update: only the second round is timed
    assert timings["api.feed_to_stream_ns"]["count"] == 2  # type: ignore[index]


class FakeRequest:
    def __init__(self, ticks: int) -> None:
        self.ticks = ticks

    async def is_disconnected(self) -> bool:
        self.ticks -= 1
        return self.ticks < 0


async def test_the_stream_opens_streams_and_closes(
    ctx: ApiContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ikars_engine.api as api

    monkeypatch.setattr(api, "PORTFOLIO_EVERY_S", 0.0)
    monkeypatch.setattr(api, "HEALTH_EVERY_S", 0.0)
    state = Stream(id="s1", quotes={"KXA"})
    _place(state, AlertIndex(ctx.alert_log), 2)  # a reconnect that saw alert 2
    ctx.streams[state.id] = state
    chunks = [chunk async for chunk in _events(ctx, state, FakeRequest(1))]  # type: ignore[arg-type]
    events = [chunk.split(b"\n")[1].decode() for chunk in chunks]
    assert events[0] == "event: opened"
    assert {"event: quote", "event: portfolio", "event: alert", "event: health"} <= set(events)
    alert = next(chunk for chunk in chunks if b"event: alert" in chunk)
    assert b'"line":3' in alert  # only the line it missed
    assert state.id not in ctx.streams  # forgotten when the client goes


def test_new_alerts_read_only_new_complete_lines(ctx: ApiContext) -> None:
    state = Stream(id="s")
    _place(state, AlertIndex(ctx.alert_log), None)  # a new stream starts at the end
    assert new_alerts(ctx.alert_log, state) == []
    with open(ctx.alert_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"key": "k4", "text": "alert 4"}) + "\n" + '{"key": "half')
    added = new_alerts(ctx.alert_log, state)
    assert [(a.line, a.text) for a in added] == [(4, "alert 4")]  # the half line waits
    _place(
        Stream(id="gone"), AlertIndex(ctx.alert_log.with_name("none.jsonl")), None
    )  # no file: no error


async def test_subscriptions_are_set_over_http(ctx: ApiContext) -> None:
    state = Stream(id="s9")
    ctx.streams["s9"] = state
    path = "/v1/stream/s9/subscriptions"
    ok = await call(ctx, "PUT", path, body={"quotes": ["KXA"], "books": []})
    assert ok.status_code == 200 and state.quotes == {"KXA"}
    assert (await call(ctx, "PUT", "/v1/stream/nope/subscriptions", body={})).status_code == 404
    assert (await call(ctx, "PUT", path, body={"quotes": 5})).status_code == 400
    unknown = await call(ctx, "PUT", path, body={"trades": ["KXA"]})
    assert unknown.status_code == 400  # an unknown field is refused
    many = {"quotes": [f"T{n}" for n in range(201)]}
    assert (await call(ctx, "PUT", path, body=many)).status_code == 400


# --- the server ---------------------------------------------------------------------------------


async def test_the_server_starts_where_no_acl_can_be_read(
    ctx: ApiContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1.R 26: off Windows the ACL check raised and the API never started."""

    def not_windows(path: Path) -> list[str]:
        raise NotImplementedError("other_readers reads a Windows ACL")

    monkeypatch.setattr("ikars_engine.api.other_readers", not_windows)

    def context_for(token: str) -> ApiContext:
        ctx.token = token
        return ctx

    server = ApiServer(context_for, tmp_path)
    await server.start()
    assert read_api_file(tmp_path) is not None
    await server.stop()


async def test_the_server_listens_on_loopback_and_cleans_up(
    ctx: ApiContext, tmp_path: Path
) -> None:
    def context_for(token: str) -> ApiContext:
        ctx.token = token
        return ctx

    server = ApiServer(context_for, tmp_path)
    await server.start()
    record = read_api_file(tmp_path)
    assert (
        record is not None
        and record["port"] == server.port
        and record["api_version"] == c.API_VERSION
    )
    base = f"http://127.0.0.1:{server.port}"
    async with httpx.AsyncClient(base_url=base) as http:
        hello = await http.get("/v1/hello", headers={"Authorization": f"Bearer {record['token']}"})
        assert hello.status_code == 200 and hello.json()["api_version"] == c.API_VERSION
        assert (await http.get("/v1/hello")).status_code == 401
    assert server.health()["port"] == server.port
    await server.stop()
    assert not (tmp_path / "api.json").exists()


# --- the M1.7 per-commit review's findings ------------------------------------------------------


def test_host_headers_with_and_without_ports() -> None:
    from ikars_engine.api import host_of

    assert host_of("127.0.0.1:5290") == "127.0.0.1" and host_of("LOCALHOST") == "localhost"
    assert host_of("[::1]") == "[::1]" and host_of("[::1]:80") == "[::1]"
    assert host_of("[::1") == "[::1"


def test_a_cut_back_alert_log_starts_the_stream_again(ctx: ApiContext) -> None:
    state = Stream(id="s")
    _place(state, AlertIndex(ctx.alert_log), None)
    ctx.alert_log.write_text(json.dumps({"key": "new", "text": "after the cut"}) + "\n", "utf-8")
    added = new_alerts(ctx.alert_log, state)
    assert [(a.line, a.text) for a in added] == [(1, "after the cut")]


async def test_the_server_starts_without_a_console(
    ctx: ApiContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live finding: under pythonw there is no stdout or stderr, and uvicorn's own logging set-up
    failed and took the engine down; it must not configure logging at all."""
    import sys

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    def context_for(token: str) -> ApiContext:
        ctx.token = token
        return ctx

    server = ApiServer(context_for, tmp_path)
    await server.start()
    assert server.port is not None and (tmp_path / "api.json").exists()
    await server.stop()


# --- the M1.7 merge review's findings -----------------------------------------------------------


async def test_stopping_the_server_ends_an_open_stream(ctx: ApiContext, tmp_path: Path) -> None:
    """Review 1: an open stream kept uvicorn waiting for ever at stop."""
    import asyncio
    import time as clock

    def context_for(token: str) -> ApiContext:
        ctx.token = token
        ctx.coalesce_s = 0.05
        return ctx

    server = ApiServer(context_for, tmp_path)
    await server.start()
    headers = {"Authorization": f"Bearer {ctx.token}"}
    seen = asyncio.Event()

    async def listen() -> None:
        async with (
            httpx.AsyncClient(base_url=f"http://127.0.0.1:{server.port}", timeout=30) as http,
            http.stream("GET", "/v1/stream", headers=headers) as stream,
        ):
            async for line in stream.aiter_lines():
                if line.startswith("event: opened"):
                    seen.set()

    reader = asyncio.create_task(listen())
    await asyncio.wait_for(seen.wait(), 5)
    began = clock.monotonic()
    await asyncio.wait_for(server.stop(), 10)
    assert clock.monotonic() - began < 5 and ctx.streams == {}
    await asyncio.wait_for(asyncio.gather(reader, return_exceptions=True), 5)


def test_the_markets_list_is_sorted_once_per_index_change(ctx: ApiContext) -> None:
    """M1.R 16: every markets request sorted the whole index."""
    from ikars_engine.api import sorted_markets

    first = sorted_markets(ctx)
    assert sorted_markets(ctx) is first  # unchanged index: the same list, not sorted again
    ctx.index.version += 1
    assert sorted_markets(ctx) is not first


def test_a_dropped_book_is_announced_once_as_not_ready(ctx: ApiContext) -> None:
    """M1.R 10: a book the engine dropped (a reconnect, a market leaving the watched set) was never
    announced, so a screen kept showing it as ready."""
    state = Stream(id="s", books={"KXA"})
    first = changes(ctx, state)
    assert b'"ready":true' in first[0]
    ctx.books.pop("KXA")  # type: ignore[attr-defined]
    dropped = changes(ctx, state)
    assert len(dropped) == 1 and b'"ready":false' in dropped[0] and b'"yes":[]' in dropped[0]
    assert changes(ctx, state) == []  # said once
    ctx.books["KXA"] = snapshot_book("KXA")  # type: ignore[index]
    ctx.book_version["KXA"] = 2  # type: ignore[index]
    assert b'"ready":true' in changes(ctx, state)[0]  # rebuilt: sent again


async def test_a_websocket_is_refused_before_anything_runs() -> None:
    """M1.R 14: the guard let every non-HTTP scope through unchecked."""
    from ikars_engine.api import Guard

    reached: list[str] = []
    sent: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        reached.append(scope["type"])

    async def send(message: Any) -> None:
        sent.append(message)

    async def receive() -> Any:
        return {}

    guard = Guard(app, TOKEN)
    await guard({"type": "websocket", "headers": []}, receive, send)
    assert reached == [] and sent == [{"type": "websocket.close", "code": 1008}]
    await guard({"type": "lifespan"}, receive, send)
    assert reached == ["lifespan"]


async def test_malformed_subscriptions_and_re_subscribing(ctx: ApiContext) -> None:
    """Review 2: malformed JSON was a bare 500. Review 3: a market dropped and added back got no
    current state, and was timed as an update."""
    state = Stream(id="s7", quotes={"KXA"})
    ctx.streams["s7"] = state
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        path = "/v1/stream/s7/subscriptions"
        bad = await http.put(path, headers=AUTH, content=b"{quotes:")
        assert bad.status_code == 400 and bad.json()["error"] == "bad_request"
        assert len(changes(ctx, state)) == 1  # KXA's current quote
        await http.put(path, headers=AUTH, json={"quotes": []})
        await http.put(path, headers=AUTH, json={"quotes": ["KXA"]})
    again = changes(ctx, state)
    assert [chunk.split(b"\n")[1] for chunk in again] == [b"event: quote"]  # sent afresh
    assert ctx.metrics.snapshot()["timings"] == {}  # neither send timed as an update


def test_alert_lines_split_on_newlines_only(ctx: ApiContext) -> None:
    """Review 4: a U+2028 in an alert split it in one reader and not the other."""
    with open(ctx.alert_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"key": "k4", "text": "odd title"}, ensure_ascii=False) + "\n")
        handle.write(json.dumps({"key": "k5", "text": "after"}) + "\n")
    index = AlertIndex(ctx.alert_log)
    found = [(a.line, a.text) for a in read_alerts(index, 3, 10)]
    assert found == [(4, "odd title"), (5, "after")]
    state = Stream(id="s")
    _place(state, index, 3)
    assert [a.line for a in new_alerts(ctx.alert_log, state)] == [4, 5]
    assert read_alerts(index, None, 1)[0].line == 5


async def test_a_stream_registers_when_it_runs_and_streams_are_capped(ctx: ApiContext) -> None:
    """Review 5: a stream never iterated stayed registered for ever; nothing capped them."""
    import ikars_engine.api as api

    for n in range(api.MAX_STREAMS):
        ctx.streams[f"busy{n}"] = Stream(id=f"busy{n}")
    response = await call(ctx, "GET", "/v1/stream")
    assert response.status_code == 503 and response.json()["error"] == "unavailable"
    ctx.streams.clear()
    state = Stream(id="lazy")
    generator = _events(ctx, state, FakeRequest(0))  # type: ignore[arg-type]
    assert "lazy" not in ctx.streams  # nothing registered until it runs
    await generator.__anext__()
    assert "lazy" in ctx.streams
    await generator.aclose()
    assert "lazy" not in ctx.streams


async def test_the_stream_cap_holds_when_streams_arrive_together(ctx: ApiContext) -> None:
    """Per-commit review: requests arriving together all passed the check before registering."""
    import ikars_engine.api as api

    for n in range(api.MAX_STREAMS):
        ctx.streams[f"busy{n}"] = Stream(id=f"busy{n}")
    late = [chunk async for chunk in _events(ctx, Stream(id="late"), FakeRequest(5))]  # type: ignore[arg-type]
    assert len(late) == 1 and b"event: error" in late[0] and "late" not in ctx.streams


# --- the candles route (M1.8a) ------------------------------------------------------------------


def made_up_candles() -> list[Any]:
    from ikars_core.models import Candlestick

    return [Candlestick.model_validate({
        "end_period_ts": 1_767_225_660,
        "price": {"open_dollars": "0.4100", "close_dollars": "0.4200",
                  "high_dollars": "0.4300", "low_dollars": "0.4000"},
        "yes_bid": {"close_dollars": "0.4100"}, "yes_ask": {"close_dollars": "0.4300"},
        "volume_fp": "25.00",
    })]  # fmt: skip


async def test_candles_are_served_cached_and_refused_cleanly(ctx: ApiContext) -> None:
    calls: list[tuple[str, str, int, int, int]] = []

    async def candles(series: str, ticker: str, start: int, end: int, interval: int) -> list[Any]:
        calls.append((series, ticker, start, end, interval))
        return made_up_candles()

    ctx.candles = candles
    page = await get(ctx, "/v1/markets/KXA/candles?interval=1&hours=2", c.CandlePage)
    assert page.interval_minutes == 1 and page.items[0].close_e6 == 420_000
    assert page.items[0].volume_e2 == 2_500 and page.items[0].end_utc.endswith("Z")
    series, ticker, start, end, interval = calls[0]
    assert (series, ticker, interval, end - start) == ("KXDEMO", "KXA", 1, 2 * 3600)
    await get(ctx, "/v1/markets/KXA/candles?interval=1&hours=2", c.CandlePage)
    assert len(calls) == 1  # the second within a minute came from the cache
    assert (await get(ctx, "/v1/markets/NOPE/candles", c.ApiError, 404)).error == "not_found"
    assert (
        await get(ctx, "/v1/markets/KXA/candles?interval=5", c.ApiError, 400)
    ).error == "bad_request"

    failures: list[str] = []

    async def failing(*args: Any) -> list[Any]:
        failures.append("asked")
        raise RuntimeError("Kalshi said no")

    ctx.candles = failing
    refused = await get(ctx, "/v1/markets/KXB/candles", c.ApiError, 503)
    assert refused.error == "unavailable" and "said no" not in refused.detail
    again = await get(ctx, "/v1/markets/KXB/candles", c.ApiError, 503)
    assert again == refused and len(failures) == 1  # a failure is not asked again within a minute
