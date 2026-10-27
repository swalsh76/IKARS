"""M1.7: the loopback API (the guard, the snapshots, the stream) and its contracts. Every market,
book, ledger record and alert here is made up."""

import json
from collections.abc import Iterator
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


async def test_the_alert_log_by_line(ctx: ApiContext) -> None:
    last_two = await get(ctx, "/v1/alerts?limit=2", c.AlertPage)
    assert [a.line for a in last_two.items] == [2, 3]
    after = await get(ctx, "/v1/alerts?after=1", c.AlertPage)
    assert [a.text for a in after.items] == ["alert 2", "alert 3"]
    assert read_alerts(ctx.alert_log.with_name("none.jsonl"), None, 5) == []


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
    _place(state, ctx.alert_log, 2)  # a reconnect that saw alert 2
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
    _place(state, ctx.alert_log, None)  # a new stream starts at the end
    assert new_alerts(ctx.alert_log, state) == []
    with open(ctx.alert_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"key": "k4", "text": "alert 4"}) + "\n" + '{"key": "half')
    added = new_alerts(ctx.alert_log, state)
    assert [(a.line, a.text) for a in added] == [(4, "alert 4")]  # the half line waits
    _place(Stream(id="gone"), ctx.alert_log.with_name("none.jsonl"), None)  # no file: no error


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
    _place(state, ctx.alert_log, None)
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
