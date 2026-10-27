"""The loopback API's payloads (PLAN D3, M1.7): defined once, here, for the engine and the terminal.

Every payload is a ``msgspec`` struct. ``python -m ikars_engine contracts export`` writes their JSON
Schema to ``contracts/ikars-api.schema.json`` and a made-up **golden example** of each to
``contracts/examples/<Name>.json``; a test fails when the committed files differ from what this
module produces, and the C# test reads every example into its C# record with unknown fields refused,
so neither side can drift without a test failing.

Money is in dollar millionths (``_e6``) and counts in contract hundredths (``_e2``), integers as
everywhere in IKARS; times are UTC ISO text or integer nanoseconds (``_ns``). Field names are
snake_case on the wire. ``API_VERSION``'s major number changes only when a change would break a
terminal built for the old one; the terminal refuses a major it does not know.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import msgspec

API_VERSION = "1.0"


class Hello(msgspec.Struct, kw_only=True):
    """``GET /v1/hello``: the version handshake (PLAN D20 G)."""

    api_version: str
    engine_version: str
    schema_sha256: str  # of the exported JSON Schema: the terminal can tell it is the same contract


class HealthView(msgspec.Struct, kw_only=True):
    """``GET /v1/health``: the engine's health as it reports it (counts and states only)."""

    at_utc: str
    parts: dict[str, Any]


class MarketView(msgspec.Struct, kw_only=True):
    ticker: str
    title: str
    event_ticker: str
    series_ticker: str
    category: str
    status: str
    close_time_utc: str | None
    volume_24h_e2: int
    yes_bid_e6: int | None  # from the live feed; None until a quote has arrived
    yes_ask_e6: int | None
    last_price_e6: int | None
    watched: bool  # the engine keeps its order book


class MarketPage(msgspec.Struct, kw_only=True):
    items: list[MarketView]
    total: int
    offset: int


class Level(msgspec.Struct, kw_only=True):
    price_e6: int
    count_e2: int


class BookView(msgspec.Struct, kw_only=True):
    """A market's order book: both sides' bids, best first (Kalshi's books are bids only)."""

    ticker: str
    ready: bool
    yes: list[Level]
    no: list[Level]


class PositionView(msgspec.Struct, kw_only=True):
    ticker: str
    title: str
    side: str
    count_e2: int
    cost_e6: int
    event_ticker: str
    series_ticker: str
    category: str
    exit_price_e6: int | None
    exit_fee_e6: int | None
    unrealised_e6: int | None


class PortfolioView(msgspec.Struct, kw_only=True):
    cash_e6: int
    cost_e6: int
    unrealised_e6: int | None  # None while any position is unpriced
    positions: list[PositionView]
    exposure_by_category: dict[str, int]
    exposure_by_event: dict[str, int]


class LedgerStatusView(msgspec.Struct, kw_only=True):
    records: dict[str, int]  # by kind
    head_seq: int
    verified: bool | None
    reconciled_exact: bool | None
    tax_zone: str


class LedgerRecordView(msgspec.Struct, kw_only=True):
    seq: int
    kind: str
    time_utc: str
    time_local: str
    source: str
    ticker: str | None
    cash_e6: int | None
    fields: dict[str, Any]
    context: dict[str, Any]
    note: str | None


class LedgerPage(msgspec.Struct, kw_only=True):
    items: list[LedgerRecordView]
    head_seq: int
    before: int | None  # pass as ?before= for the next (older) page; None: no more


class AlertView(msgspec.Struct, kw_only=True):
    line: int  # its line in the alert log: ?after= resumes from here
    key: str
    priority: str
    title: str
    text: str
    at: str
    outcome: str


class AlertPage(msgspec.Struct, kw_only=True):
    items: list[AlertView]


class RecommendationView(msgspec.Struct, kw_only=True):
    """Filled from M1.12; the shape the Overview will show (provenance, PLAN D20 F, comes then)."""

    id: str
    ticker: str
    side: str
    summary: str
    at_utc: str


class RecommendationPage(msgspec.Struct, kw_only=True):
    items: list[RecommendationView]


class Candle(msgspec.Struct, kw_only=True):
    """One period of a market's prices (Kalshi's candlestick): the traded band (None when nothing
    traded), the closing YES bid and ask, and the contracts traded."""

    end_utc: str
    open_e6: int | None
    high_e6: int | None
    low_e6: int | None
    close_e6: int | None
    bid_close_e6: int | None
    ask_close_e6: int | None
    volume_e2: int | None


class CandlePage(msgspec.Struct, kw_only=True):
    """``GET /v1/markets/{ticker}/candles``: oldest first."""

    ticker: str
    interval_minutes: int
    items: list[Candle]


class Subscriptions(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """``PUT /v1/stream/{id}/subscriptions``: the markets whose quotes and books to stream."""

    quotes: list[str] = []
    books: list[str] = []


class StreamOpened(msgspec.Struct, kw_only=True):
    """The first event of every stream: its id, for ``PUT .../subscriptions``."""

    stream_id: str
    coalesce_ms: int


class QuoteUpdate(msgspec.Struct, kw_only=True):
    ticker: str
    yes_bid_e6: int | None
    yes_ask_e6: int | None
    last_price_e6: int | None
    received_ns: int | None  # when the engine received what changed it


class ApiError(msgspec.Struct, kw_only=True):
    error: str  # a short code: unauthorised, not_found, bad_request, unavailable
    detail: str


CONTRACTS: tuple[type[msgspec.Struct], ...] = (
    Hello, HealthView, MarketView, MarketPage, Level, BookView, PositionView, PortfolioView,
    LedgerStatusView, LedgerRecordView, LedgerPage, AlertView, AlertPage, Candle, CandlePage,
    RecommendationView,
    RecommendationPage, Subscriptions, StreamOpened, QuoteUpdate, ApiError,
)  # fmt: skip

# The stream's event names and the payload each carries.
STREAM_EVENTS: dict[str, type[msgspec.Struct]] = {
    "opened": StreamOpened,
    "quote": QuoteUpdate,
    "book": BookView,
    "portfolio": PortfolioView,
    "alert": AlertView,
    "health": HealthView,
    "error": ApiError,  # the stream could not open (too many at once), then it ends
}


def schema_text() -> str:
    """The JSON Schema of every contract, as committed text (sorted, two-space indent)."""
    _, components = msgspec.json.schema_components(CONTRACTS)
    document = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "IKARS loopback API",
        "version": API_VERSION,
        "stream_events": {name: kind.__name__ for name, kind in STREAM_EVENTS.items()},
        "$defs": components,
    }
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def schema_sha256() -> str:
    return hashlib.sha256(schema_text().encode("utf-8")).hexdigest()


def examples() -> dict[str, Any]:
    """A made-up example of every contract (the golden examples)."""
    level = Level(price_e6=410_000, count_e2=1_250)
    market = MarketView(
        ticker="KXDEMO-26JAN01-T50",
        title="Made-up market",
        event_ticker="KXDEMO-26JAN01",
        series_ticker="KXDEMO",
        category="Demo",
        status="active",
        close_time_utc="2026-01-01T12:00:00Z",
        volume_24h_e2=100_000,
        yes_bid_e6=410_000,
        yes_ask_e6=430_000,
        last_price_e6=420_000,
        watched=True,
    )
    position = PositionView(
        ticker=market.ticker,
        title=market.title,
        side="yes",
        count_e2=300,
        cost_e6=1_518_000,
        event_ticker=market.event_ticker,
        series_ticker="KXDEMO",
        category="Demo",
        exit_price_e6=550_000,
        exit_fee_e6=60_000,
        unrealised_e6=72_000,
    )
    record = LedgerRecordView(
        seq=1,
        kind="deposit",
        time_utc="2026-01-01T00:00:00.000000000Z",
        time_local="2025-12-31T17:00:00-07:00",
        source="deposit",
        ticker=None,
        cash_e6=100_000_000,
        fields={"status": "applied", "amount_e6": 100_000_000},
        context={},
        note=None,
    )
    alert = AlertView(
        line=1,
        key="watchdog:engine",
        priority="urgent",
        title="problem",
        text="the engine is not running",
        at="2026-01-01T12:00:00-07:00",
        outcome="shown",
    )
    candle = Candle(
        end_utc="2026-01-01T12:01:00Z", open_e6=410_000, high_e6=430_000, low_e6=400_000,
        close_e6=420_000, bid_close_e6=410_000, ask_close_e6=430_000, volume_e2=2_500,
    )  # fmt: skip
    found: dict[str, Any] = {
        "Hello": Hello(api_version=API_VERSION, engine_version="0.0.0", schema_sha256="0" * 64),
        "HealthView": HealthView(at_utc="2026-01-01T00:00:00Z", parts={"ledger": {"records": 1}}),
        "MarketView": market,
        "MarketPage": MarketPage(items=[market], total=1, offset=0),
        "Level": level,
        "BookView": BookView(ticker=market.ticker, ready=True, yes=[level], no=[level]),
        "PositionView": position,
        "PortfolioView": PortfolioView(
            cash_e6=100_000_000,
            cost_e6=1_518_000,
            unrealised_e6=72_000,
            positions=[position],
            exposure_by_category={"Demo": 1_518_000},
            exposure_by_event={"KXDEMO-26JAN01": 1_518_000},
        ),
        "LedgerStatusView": LedgerStatusView(
            records={"deposit": 1},
            head_seq=1,
            verified=True,
            reconciled_exact=True,
            tax_zone="America/Denver",
        ),
        "LedgerRecordView": record,
        "LedgerPage": LedgerPage(items=[record], head_seq=1, before=None),
        "AlertView": alert,
        "AlertPage": AlertPage(items=[alert]),
        "RecommendationView": RecommendationView(
            id="r-1",
            ticker=market.ticker,
            side="yes",
            summary="made up",
            at_utc="2026-01-01T00:00:00Z",
        ),
        "RecommendationPage": RecommendationPage(items=[]),
        "Candle": candle,
        "CandlePage": CandlePage(ticker=market.ticker, interval_minutes=1, items=[candle]),
        "Subscriptions": Subscriptions(quotes=[market.ticker], books=[market.ticker]),
        "StreamOpened": StreamOpened(stream_id="s-1", coalesce_ms=100),
        "QuoteUpdate": QuoteUpdate(
            ticker=market.ticker,
            yes_bid_e6=410_000,
            yes_ask_e6=430_000,
            last_price_e6=420_000,
            received_ns=1_767_225_600_000_000_000,
        ),
        "ApiError": ApiError(error="not_found", detail="no such market"),
    }
    return found


def export(folder: Path) -> list[Path]:
    """Write the schema and the golden examples under ``folder`` (the repository's
    ``contracts``)."""
    written = [folder / "ikars-api.schema.json"]
    written[0].parent.mkdir(parents=True, exist_ok=True)
    written[0].write_text(schema_text(), encoding="utf-8", newline="\n")
    for name, value in examples().items():
        path = folder / "examples" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(msgspec.to_builtins(value), indent=2, sort_keys=True) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
