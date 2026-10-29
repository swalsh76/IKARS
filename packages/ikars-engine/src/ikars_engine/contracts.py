"""The loopback API's payloads (PLAN D3, M1.7): defined once, here, for the engine and the terminal.

Every payload is a ``msgspec`` struct. ``python -m ikars_engine contracts export`` writes their JSON
Schema to ``contracts/ikars-api.schema.json`` and a made-up **golden example** of each to
``contracts/examples/<Name>.json``; a test fails when the committed files differ from what this
module produces. From M1.8a-web the web terminal's types are generated from that schema and its own
test reads every example with unknown fields refused (D3 as amended 2026-10-27), so neither side can
drift without a test failing; until then only this engine-side test guards the contracts.

Money is in dollar millionths (``_e6``) and counts in contract hundredths (``_e2``), integers as
everywhere in IKARS. **Every integer on the wire is exact in JavaScript (at most 2^53)** except the
``_ns`` times: money to about $9 billion, counts to about 90 trillion contracts, and the small
numbers in ``SMALL_INTEGERS``; a test holds every integer field to one of these. A browser reads an
``_ns`` time to about a quarter of a microsecond, which no screen needs more finely.

**Times** (review 1.R2 item 4): ``*_utc`` is ISO 8601 UTC ending in ``Z`` (to the second, or to the
nanosecond where the record keeps it: the ledger); ``*_local`` is ISO 8601 with its offset (the
workstation's zone); ``*_ns`` is integer nanoseconds since the epoch. Field names are snake_case on
the wire. ``API_VERSION``'s major number changes only when a change would break a terminal built
for the old one; the engine serves its own terminal, so the two always match.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import msgspec

from ikars_engine.settings import Layout, LayoutWindow, Skin

API_VERSION = "2.2"  # 2.0: typed health, AlertView.at_local, sign-in; 2.1: ranked recommendations;
# 2.2: POST /v1/backup (Back up now) and BackupHealth.running


class Hello(msgspec.Struct, kw_only=True):
    """``GET /v1/hello``: the version handshake (PLAN D20 G)."""

    api_version: str
    engine_version: str
    schema_sha256: str  # of the exported JSON Schema: the terminal can tell it is the same contract


class FeedHealth(msgspec.Struct, kw_only=True):
    connected: bool | None
    seconds_since_last_frame: float | None
    open_gaps: int | None
    books_ready: int | None
    watched_markets: int | None
    trading_active: bool | None  # the exchange says trading is open


class LedgerHealth(msgspec.Struct, kw_only=True):
    verified: bool | None
    reconciled_exact: bool | None
    records: int | None
    errors: int | None


class BackupHealth(msgspec.Struct, kw_only=True):
    configured: bool | None
    hours_since: int | None
    last_error: str | None
    running: bool | None = None  # 2.2: a backup is being made now (the owner's or the day's)


class HealthView(msgspec.Struct, kw_only=True):
    """``GET /v1/health`` and the stream's ``health`` event: what the terminal shows, typed (None
    where the engine has not measured it yet), and the engine's whole report in ``detail`` for the
    Health screen's details (counts and states only)."""

    at_utc: str
    feed: FeedHealth
    storage_state: str | None
    ledger: LedgerHealth
    backup: BackupHealth
    clock_status: str | None
    clock_offset_ms: float | None
    gpu: str | None
    api_streams: int | None
    port_fallback: bool | None  # the fixed port was taken: a terminal cannot reconnect by itself
    setup_reason: str | None  # setup mode (M1.9a): why the Kalshi key cannot be used; None: in full
    plugins: dict[str, str]
    detail: dict[str, Any]


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
    at_local: str
    outcome: str


class AlertPage(msgspec.Struct, kw_only=True):
    items: list[AlertView]


class MarkView(msgspec.Struct, kw_only=True):
    """A recommendation's market at one of its horizons (the score after fees comes from it)."""

    horizon_s: int
    at_utc: str
    yes_bid_e6: int | None
    yes_ask_e6: int | None
    score_e6: int | None  # sold at the side's bid then, after both fees (None: no bid or fee)
    at_settlement: bool  # no bid then, the market settled: scored at $1 or $0, no exit fee


class RecommendationView(msgspec.Struct, kw_only=True):
    """A recommendation from the journal (M1.12): what was advised, by which rule (its registered
    version, G4), from what (provenance, F), its lifecycle (G9) and its marks."""

    id: str
    ticker: str
    event_ticker: str
    side: str
    summary: str
    at_utc: str  # when IKARS made it
    strategy: str
    version: str
    state: str  # active, expired, superseded
    superseded_by: str | None
    entry_price_e6: int
    count_e2: int
    expires_utc: str
    pnl_e6: int | None  # after fees on entry and exit (M1.12b)
    inputs: list[str]  # the items it was built from ("kind:id")
    marks: list[MarkView]
    # M1.12c
    title: str  # the market's ("" when the index no longer has it)
    source: str  # the plugin that made it
    outlay_e6: int | None  # the contracts and the entry fee: also the worst case (None: no fees)
    return_e6: int | None  # at the target after fees, in millionths of the outlay
    per_day_e6: int | None  # that, per day of its validity: the ranking (D20 D)
    response: str | None  # the owner's: taken, ignored or dismissed
    horizons_s: list[int]  # when it is checked after it is made (marks come at these)


class RecommendationPage(msgspec.Struct, kw_only=True):
    """The newest, or (``state=active`` and the stream's ``recommendations`` event) the active ones
    ranked by return per day, the dismissed left out; ``total`` is how many there are."""

    items: list[RecommendationView]
    total: int


class ResponseChange(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """``PUT /v1/recommendations/{id}/response``: the owner's response (None takes it back)."""

    answer: Literal["taken", "ignored", "dismissed"] | None


class ScoreLineView(msgspec.Struct, kw_only=True):
    """One strategy version's checks at one horizon, after costs."""

    strategy: str
    version: str
    horizon_s: int
    checks: int  # made (scored, unscorable, or waiting for a settlement)
    scored: int
    above_zero: int
    mean_e6: int | None
    median_e6: int | None


class ScoreCard(msgspec.Struct, kw_only=True):
    """``GET /v1/scorecard``: the recommendations made since ``since_utc`` (30 days), worked out
    once a minute (``at_utc``; None before the first)."""

    since_utc: str
    at_utc: str | None
    lines: list[ScoreLineView]


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


class GapView(msgspec.Struct, kw_only=True):
    """A loss the engine logged (PLAN M1.4b): what was missed, and what became of it."""

    id: int
    channel: str
    reason: str
    start_utc: str
    end_utc: str
    recovery: str  # backfill, discovery or unrecoverable
    status: str  # open, done, failed or unrecoverable
    detail: str


class GapPage(msgspec.Struct, kw_only=True):
    """``GET /v1/gaps``: newest first."""

    items: list[GapView]


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


class SessionCode(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """``POST /v1/session``: the terminal trades its one-time code for the session cookie."""

    code: str


class SessionCodeIssued(msgspec.Struct, kw_only=True):
    """``POST /v1/session/code`` (the bearer token only, for ``ikars open``): a one-time code, and
    the address that opens the terminal with it."""

    code: str
    url: str


class KeyStatus(msgspec.Struct, kw_only=True):
    """IKARS's Kalshi key, as yes or no only: never a key, its id or its path (PLAN D8)."""

    id_set: bool  # the key id's environment variable is set
    path_set: bool  # the key path's environment variable is set
    file_found: bool  # the key file is there
    owner_only: bool | None  # only this account can read it (None: could not be told)
    environment: str  # production or demo
    needs_restart: bool  # set in Windows since this engine started: a restart uses it


class SettingsView(msgspec.Struct, kw_only=True):
    """``GET /v1/settings`` (M1.8b-1): what the Settings screen and the first-run wizard show."""

    first_run: bool  # no settings file yet: the wizard opens
    skin: Skin
    quiet_start: str  # HH:MM, workstation time
    quiet_end: str
    throttle_minutes: int
    digest_minutes: int
    tax_time_zone: str  # an IANA name; empty: the workstation's zone
    tax_zone_effective: str  # the zone in use
    backup_share: str  # empty: no backups
    watched_max_markets: int
    read_share_percent: int
    storage_budget_gb: int
    port: int
    session_days: int
    restart_needed: list[str]  # saved settings the running engine does not use yet
    key: KeyStatus
    autostart_installed: bool  # the engine starts at sign-in, with its watchdog


class SettingsChange(msgspec.Struct, kw_only=True, forbid_unknown_fields=True, omit_defaults=True):
    """``PUT /v1/settings``: the settings to change (any left out stay as they are)."""

    skin: Skin | None = None
    quiet_start: str | None = None
    quiet_end: str | None = None
    throttle_minutes: int | None = None
    digest_minutes: int | None = None
    tax_time_zone: str | None = None
    backup_share: str | None = None
    watched_max_markets: int | None = None
    read_share_percent: int | None = None
    storage_budget_gb: int | None = None
    port: int | None = None
    session_days: int | None = None


class ShareCheck(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """``POST /v1/settings/share-check`` and ``/share-init``: the share to check or set up."""

    share: str


class ShareCheckView(msgspec.Struct, kw_only=True):
    reachable: bool
    marker: bool  # IKARS's own folder and marker are there
    writable: bool | None  # a test file in IKARS's folder could be written (None: no folder yet)
    problem: str | None


class Done(msgspec.Struct, kw_only=True):
    """An action's answer (restart the engine, start at sign-in)."""

    done: bool
    detail: str


class LayoutsView(msgspec.Struct, kw_only=True):
    """``GET /v1/layouts``: the saved layouts (each a set of IKARS windows; ``PUT /v1/layouts``
    saves a ``Layout``) and the one ``ikars open`` restores (empty: one window)."""

    layouts: list[Layout]
    start: str


class LayoutStart(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """``PUT /v1/layouts/start``: the layout ``ikars open`` restores (empty: one window)."""

    name: str


class UpdateView(msgspec.Struct, kw_only=True):
    """``GET /v1/update`` (and ``POST /v1/update/check``): the program's version and any newer one,
    downloaded and waiting for the owner's Restart to update (``POST /v1/update/apply``)."""

    installed: bool  # False: a development checkout, which has no updates
    current: str | None
    available: str | None
    state: str  # not installed, up to date, checking, downloading, ready, failed
    checked_at_utc: str | None
    problem: str | None
    progress: int | None  # the download, in percent (Velopack's); None: no download yet
    size_bytes: int | None  # what the download fetches (a delta when there is one)
    events: list[str]  # the last steps, "HH:MM:SS text" (UTC), for the Update window's Details


class ApiError(msgspec.Struct, kw_only=True):
    error: str  # a short code: unauthorised, not_found, bad_request, unavailable
    detail: str


# Integer fields that are neither money (_e6), counts (_e2) nor times (_ns): small by nature, so
# exact in JavaScript. A new integer field must be one of these four (a test checks).
SMALL_INTEGERS = frozenset({
    "api_streams", "before", "books_ready", "coalesce_ms", "errors", "head_seq", "hours_since",
    "id", "interval_minutes", "line", "offset", "open_gaps", "records", "seq", "total",
    "watched_markets", "throttle_minutes", "digest_minutes", "watched_max_markets",
    "read_share_percent", "storage_budget_gb", "port", "session_days", "x", "y", "width",
    "height", "progress", "size_bytes", "horizon_s", "horizons_s", "checks", "scored", "above_zero",
})  # fmt: skip

CONTRACTS: tuple[type[msgspec.Struct], ...] = (
    Hello, FeedHealth, LedgerHealth, BackupHealth, HealthView, MarketView, MarketPage, Level,
    BookView, PositionView, PortfolioView,
    LedgerStatusView, LedgerRecordView, LedgerPage, AlertView, AlertPage, Candle, CandlePage,
    GapView, GapPage,
    RecommendationView,
    MarkView, RecommendationPage, ResponseChange, ScoreLineView, ScoreCard, Subscriptions,
    StreamOpened, QuoteUpdate, SessionCode,
    SessionCodeIssued,
    KeyStatus, SettingsView, SettingsChange, ShareCheck, ShareCheckView, Done,
    LayoutWindow, Layout, LayoutsView, LayoutStart, UpdateView, ApiError,
)  # fmt: skip

# The stream's event names and the payload each carries.
STREAM_EVENTS: dict[str, type[msgspec.Struct]] = {
    "opened": StreamOpened,
    "quote": QuoteUpdate,
    "book": BookView,
    "portfolio": PortfolioView,
    "alert": AlertView,
    "health": HealthView,
    "recommendations": RecommendationPage,  # the active ones, ranked, when they change (M1.12c)
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
    mark = MarkView(horizon_s=1800, at_utc="2026-01-01T00:30:00Z", yes_bid_e6=440_000,
                    yes_ask_e6=450_000, score_e6=60_000, at_settlement=False)  # fmt: skip
    recommendation = RecommendationView(
        id="momentum-KXDEMO-26JAN01-T50-1", ticker="KXDEMO-26JAN01-T50",
        event_ticker="KXDEMO-26JAN01", side="yes", summary="made up: up 5c in 5 min on 120",
        at_utc="2026-01-01T00:00:00Z", strategy="momentum", version="9f2ce1a0", state="active",
        superseded_by=None, entry_price_e6=420_000, count_e2=1_000,
        expires_utc="2026-01-01T00:30:00Z", pnl_e6=20_000, inputs=["market:KXDEMO-26JAN01-T50"],
        marks=[mark], title="Made-up market above 50", source="momentum", outlay_e6=4_270_000,
        return_e6=4_683, per_day_e6=224_784, response=None, horizons_s=[1800, 3600],
    )  # fmt: skip
    line = ScoreLineView(strategy="momentum", version="9f2ce1a0", horizon_s=1800, checks=12,
                         scored=10, above_zero=4, mean_e6=-42_000, median_e6=-28_000)  # fmt: skip
    window = LayoutWindow(screen="/markets?q=fed", x=-1920, y=0, width=1600, height=1000)
    second = LayoutWindow(screen="/overview", x=0, y=0, width=1280, height=800)
    layout = Layout(name="Trading", windows=[window, second])
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
        at_local="2026-01-01T12:00:00-07:00",
        outcome="shown",
    )
    candle = Candle(
        end_utc="2026-01-01T12:01:00Z", open_e6=410_000, high_e6=430_000, low_e6=400_000,
        close_e6=420_000, bid_close_e6=410_000, ask_close_e6=430_000, volume_e2=2_500,
    )  # fmt: skip
    key = KeyStatus(
        id_set=True, path_set=True, file_found=True, owner_only=True, environment="production",
        needs_restart=False,
    )  # fmt: skip
    feed = FeedHealth(
        connected=True, seconds_since_last_frame=0.2, open_gaps=0, books_ready=100,
        watched_markets=100, trading_active=True,
    )  # fmt: skip
    ledger = LedgerHealth(verified=True, reconciled_exact=True, records=183, errors=0)
    backup = BackupHealth(configured=True, hours_since=3, last_error=None, running=False)
    gap = GapView(
        id=1, channel="trade", reason="reconnect (connection_lost)",
        start_utc="2026-01-01T00:00:00Z", end_utc="2026-01-01T00:00:03Z", recovery="backfill",
        status="done", detail="71 trades fetched",
    )  # fmt: skip
    found: dict[str, Any] = {
        "Hello": Hello(api_version=API_VERSION, engine_version="0.0.0", schema_sha256="0" * 64),
        "FeedHealth": feed,
        "LedgerHealth": ledger,
        "BackupHealth": backup,
        "HealthView": HealthView(
            at_utc="2026-01-01T00:00:00Z",
            feed=feed,
            storage_state="ok",
            ledger=ledger,
            backup=backup,
            clock_status="ok",
            clock_offset_ms=-3.5,
            gpu="Made-up GPU",
            api_streams=1,
            port_fallback=False,
            setup_reason=None,
            plugins={"demo": "running"},
            detail={"kalshi": {"frames": {"ticker": 10}}},
        ),  # fmt: skip
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
        "MarkView": mark,
        "RecommendationView": recommendation,
        "RecommendationPage": RecommendationPage(items=[recommendation], total=1),
        "ResponseChange": ResponseChange(answer="taken"),
        "ScoreLineView": line,
        "ScoreCard": ScoreCard(
            since_utc="2025-12-02T00:00:00Z", at_utc="2026-01-01T00:00:00Z", lines=[line]
        ),
        "Candle": candle,
        "CandlePage": CandlePage(ticker=market.ticker, interval_minutes=1, items=[candle]),
        "GapView": gap,
        "GapPage": GapPage(items=[gap]),
        "Subscriptions": Subscriptions(quotes=[market.ticker], books=[market.ticker]),
        "StreamOpened": StreamOpened(stream_id="s-1", coalesce_ms=100),
        "QuoteUpdate": QuoteUpdate(
            ticker=market.ticker,
            yes_bid_e6=410_000,
            yes_ask_e6=430_000,
            last_price_e6=420_000,
            received_ns=1_767_225_600_000_000_000,
        ),
        "SessionCode": SessionCode(code="made-up-code"),
        "SessionCodeIssued": SessionCodeIssued(
            code="made-up-code", url="http://127.0.0.1:47821/#code=made-up-code"
        ),
        "KeyStatus": key,
        "SettingsView": SettingsView(
            first_run=False,
            skin="system",
            quiet_start="22:00",
            quiet_end="07:00",
            throttle_minutes=10,
            digest_minutes=60,
            tax_time_zone="",
            tax_zone_effective="America/Denver",
            backup_share="",
            watched_max_markets=100,
            read_share_percent=25,
            storage_budget_gb=200,
            port=47821,
            session_days=30,
            restart_needed=["port"],
            key=key,
            autostart_installed=True,
        ),  # fmt: skip
        "SettingsChange": SettingsChange(skin="midnight", quiet_start="23:00"),
        "ShareCheck": ShareCheck(share="made-up-share"),
        "ShareCheckView": ShareCheckView(reachable=True, marker=True, writable=True, problem=None),
        "Done": Done(done=True, detail="the engine restarts"),
        "LayoutWindow": window,
        "Layout": layout,
        "LayoutsView": LayoutsView(layouts=[layout], start="Trading"),
        "LayoutStart": LayoutStart(name="Trading"),
        "UpdateView": UpdateView(
            installed=True,
            current="0.1.0",
            available="0.1.1",
            state="ready",
            checked_at_utc="2026-01-01T12:00:00+00:00",
            problem=None,
            progress=100,
            size_bytes=4_200_000,
            events=["12:00:01 downloading 0.1.1 (4.2 MB)", "12:00:09 downloaded 0.1.1"],
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
