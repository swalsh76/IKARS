"""Typed access to Kalshi's public market-data endpoints, over the GET-only client.

Listings that page yield :class:`TypedPage` objects that keep the cursors and the original JSON
of each row, so the downloader can resume an interrupted listing and keep a copy of what it
could not model. Parameters are checked against Kalshi's documented rules before a request is
made, so a bad combination fails here and not as an HTTP 400 after the rate limiter has been paid.
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ikars_core.models import (
    Candlestick,
    Event,
    ExchangeSchedule,
    ExchangeStatus,
    HistoricalCutoff,
    Market,
    Model,
    Series,
    SeriesFeeChange,
    Trade,
)
from ikars_core.rest import KalshiPublicClient, KalshiResponseError

MARKET_STATUSES = frozenset({"unopened", "open", "paused", "closed", "settled"})
MVE_FILTERS = frozenset({"exclude", "only"})
CANDLE_INTERVALS = frozenset({1, 60, 1440})  # minutes
MAX_PAGE_SIZE = 1000

# Live /markets timestamp filters come in groups that cannot be combined; each group needs a
# compatible status (or none).
_TS_GROUPS: dict[str, tuple[frozenset[str], frozenset[str | None]]] = {
    "created": (
        frozenset({"min_created_ts", "max_created_ts"}),
        frozenset({None, "unopened", "open"}),
    ),
    "close": (frozenset({"min_close_ts", "max_close_ts"}), frozenset({None, "closed"})),
    "settled": (frozenset({"min_settled_ts", "max_settled_ts"}), frozenset({None, "settled"})),
    "updated": (frozenset({"min_updated_ts", "max_updated_ts"}), frozenset({None})),
}


class ParameterError(ValueError):
    """A request parameter is outside what Kalshi documents; nothing was sent."""


@dataclass(frozen=True, slots=True)
class TypedPage[T]:
    items: list[T]
    raw: list[dict[str, Any]]  # the original JSON of each item, in the same order
    request_cursor: str | None
    next_cursor: str | None
    invalid: list[tuple[dict[str, Any], str]] = field(default_factory=list)


def _timestamp(name: str, value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ParameterError(f"{name} must be a non-negative integer (Unix seconds), got {value!r}")
    return value


def _range(low_name: str, low: int | None, high_name: str, high: int | None) -> None:
    _timestamp(low_name, low)
    _timestamp(high_name, high)
    if low is not None and high is not None and low > high:
        raise ParameterError(f"{low_name} ({low}) is after {high_name} ({high})")


def _limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ParameterError(f"limit must be between 1 and {MAX_PAGE_SIZE}, got {limit!r}")
    return limit


def _choice(name: str, value: str | None, allowed: frozenset[str]) -> str | None:
    if value is not None and value not in allowed:
        raise ParameterError(f"{name} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _parse[M: Model](model: type[M], raw: Mapping[str, Any], where: str) -> M:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_input=False)[:3]
        )
        ident = raw.get("ticker") or raw.get("trade_id") or raw.get("event_ticker") or raw.get("id")
        raise KalshiResponseError(
            f"{where}: cannot read {model.__name__} {ident!r}: {problems}"
        ) from None


def _rows[M: Model](model: type[M], payload: Mapping[str, Any], key: str, where: str) -> list[M]:
    """The list of objects under ``key``, each read into ``model``."""
    rows = payload.get(key)
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise KalshiResponseError(f"{where}: {key!r} is not a list of objects")
    return [_parse(model, row, where) for row in rows]


class Reader:
    """Typed reads over a GET-only client: one object, or pages of typed rows. Shared by the market
    data and the account readers."""

    def __init__(self, client: KalshiPublicClient) -> None:
        self._client = client

    async def _object[M: Model](
        self, path: str, key: str | None, model: type[M], params: Mapping[str, Any] | None = None
    ) -> M:
        payload = await self._client.get_json(path, params)
        body = payload if key is None else payload.get(key)
        if not isinstance(body, dict):
            raise KalshiResponseError(
                f"GET {path}: expected an object" + (f" under {key!r}" if key else "")
            )
        return _parse(model, body, f"GET {path}")

    async def _typed_pages[M: Model](
        self,
        path: str,
        items_key: str,
        model: type[M],
        params: Mapping[str, Any],
        cursor: str | None,
        tolerant: bool = False,
    ) -> AsyncIterator[TypedPage[M]]:
        """Pages of typed rows. A row that cannot be read is an error, unless ``tolerant``: then it
        is set aside, with the reason, in the page's ``invalid`` list and the rest are returned."""
        async for page in self._client.pages(path, items_key, params, cursor=cursor):
            items: list[M] = []
            raws: list[dict[str, Any]] = []
            invalid: list[tuple[dict[str, Any], str]] = []
            for row in page.items:
                try:
                    items.append(_parse(model, row, f"GET {path}"))
                    raws.append(row)
                except KalshiResponseError as exc:
                    if not tolerant:
                        raise
                    invalid.append((row, str(exc)))
            yield TypedPage(items, raws, page.request_cursor, page.next_cursor, invalid)


class MarketData(Reader):
    # --- single reads -----------------------------------------------------------------------

    async def exchange_status(self) -> ExchangeStatus:
        return await self._object("/exchange/status", None, ExchangeStatus)

    async def exchange_schedule(self) -> ExchangeSchedule:
        return await self._object("/exchange/schedule", "schedule", ExchangeSchedule)

    async def historical_cutoff(self) -> HistoricalCutoff:
        return await self._object("/historical/cutoff", None, HistoricalCutoff)

    async def market(self, ticker: str) -> Market:
        return await self._object(f"/markets/{ticker}", "market", Market)

    async def historical_market(self, ticker: str) -> Market:
        return await self._object(f"/historical/markets/{ticker}", "market", Market)

    async def event(self, event_ticker: str) -> Event:
        return await self._object(f"/events/{event_ticker}", "event", Event)

    async def series(self, series_ticker: str) -> Series:
        return await self._object(f"/series/{series_ticker}", "series", Series)

    async def series_list(self) -> list[Series]:
        """Every series in one response (about 15,000 series, 20 MB; the endpoint does not page)."""
        payload = await self._client.get_json("/series")
        return _rows(Series, payload, "series", "GET /series")

    async def series_fee_changes(
        self, series_ticker: str | None = None, *, show_historical: bool = True
    ) -> list[SeriesFeeChange]:
        """Scheduled and past fee changes; with ``show_historical`` the past ones too."""
        payload = await self._client.get_json(
            "/series/fee_changes",
            {
                "series_ticker": series_ticker,
                "show_historical": "true" if show_historical else "false",
            },
        )
        return _rows(SeriesFeeChange, payload, "series_fee_change_arr", "GET /series/fee_changes")

    # --- listings that page -----------------------------------------------------------------

    def historical_markets(
        self,
        *,
        limit: int = MAX_PAGE_SIZE,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        tickers: str | None = None,
        mve_filter: str | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[TypedPage[Market]]:
        """Markets that settled before the historical cutoff."""
        params = {
            "limit": _limit(limit),
            "series_ticker": series_ticker,
            "event_ticker": event_ticker,
            "tickers": tickers,
            "mve_filter": _choice("mve_filter", mve_filter, MVE_FILTERS),
        }
        return self._typed_pages("/historical/markets", "markets", Market, params, cursor)

    def markets(
        self,
        *,
        status: str | None = None,
        limit: int = MAX_PAGE_SIZE,
        series_ticker: str | None = None,
        event_ticker: str | None = None,
        tickers: str | None = None,
        mve_filter: str | None = None,
        cursor: str | None = None,
        tolerant: bool = False,
        **timestamps: int | None,
    ) -> AsyncIterator[TypedPage[Market]]:
        """Live markets. ``timestamps`` are the documented ``min_*_ts`` / ``max_*_ts`` filters;
        only one group (created, close, settled or updated) may be used, with a compatible
        status."""
        _choice("status", status, MARKET_STATUSES)
        mve_filter = _choice("mve_filter", mve_filter, MVE_FILTERS)
        given = {k: v for k, v in timestamps.items() if v is not None}
        used = {g for g, (names, _) in _TS_GROUPS.items() if names & given.keys()}
        unknown = given.keys() - set().union(*(names for names, _ in _TS_GROUPS.values()))
        if unknown:
            raise ParameterError(f"unknown timestamp filter(s): {sorted(unknown)}")
        if len(used) > 1:
            raise ParameterError(
                f"timestamp filters from different groups cannot be combined: {sorted(used)}"
            )
        for group in used:
            names, statuses = _TS_GROUPS[group]
            if status not in statuses:
                raise ParameterError(
                    f"the {group} timestamp filters need status in {sorted(map(str, statuses))}"
                )
            low, high = f"min_{group}_ts", f"max_{group}_ts"
            _range(low, given.get(low), high, given.get(high))
        if "updated" in used and series_ticker is not None and mve_filter != "exclude":
            raise ParameterError(
                "series_ticker with updated timestamps requires mve_filter='exclude'"
            )
        params = {
            "status": status,
            "limit": _limit(limit),
            "series_ticker": series_ticker,
            "event_ticker": event_ticker,
            "tickers": tickers,
            "mve_filter": mve_filter,
            **given,
        }
        return self._typed_pages("/markets", "markets", Market, params, cursor, tolerant)

    def _trades(
        self,
        path: str,
        ticker: str | None,
        min_ts: int | None,
        max_ts: int | None,
        limit: int,
        is_block_trade: bool | None,
        cursor: str | None,
    ) -> AsyncIterator[TypedPage[Trade]]:
        _range("min_ts", min_ts, "max_ts", max_ts)
        params = {
            "ticker": ticker,
            "min_ts": min_ts,
            "max_ts": max_ts,
            "limit": _limit(limit),
            "is_block_trade": None if is_block_trade is None else str(is_block_trade).lower(),
        }
        return self._typed_pages(path, "trades", Trade, params, cursor)

    def historical_trades(
        self,
        *,
        ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        limit: int = MAX_PAGE_SIZE,
        is_block_trade: bool | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[TypedPage[Trade]]:
        """Trades before the historical cutoff, newest first. The bounds are whole Unix seconds:
        ``min_ts`` is inclusive and ``max_ts`` exclusive (measured live, K0.5.3c)."""
        return self._trades(
            "/historical/trades", ticker, min_ts, max_ts, limit, is_block_trade, cursor
        )

    def trades(
        self,
        *,
        ticker: str | None = None,
        min_ts: int | None = None,
        max_ts: int | None = None,
        limit: int = MAX_PAGE_SIZE,
        is_block_trade: bool | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[TypedPage[Trade]]:
        """Live trades (after the historical cutoff). ``max_ts`` is exclusive (LESSONS.md)."""
        return self._trades(
            "/markets/trades", ticker, min_ts, max_ts, limit, is_block_trade, cursor
        )

    # --- candlesticks -----------------------------------------------------------------------

    async def _candles(
        self, path: str, start_ts: int, end_ts: int, period_interval: int
    ) -> list[Candlestick]:
        _range("start_ts", start_ts, "end_ts", end_ts)
        if period_interval not in CANDLE_INTERVALS or isinstance(period_interval, bool):
            raise ParameterError(
                f"period_interval must be one of {sorted(CANDLE_INTERVALS)} minutes"
            )
        payload = await self._client.get_json(
            path, {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval}
        )
        return _rows(Candlestick, payload, "candlesticks", f"GET {path}")

    async def historical_candlesticks(
        self, ticker: str, *, start_ts: int, end_ts: int, period_interval: int
    ) -> list[Candlestick]:
        """Candlesticks of a market settled before the cutoff (interval 1, 60 or 1440 minutes)."""
        return await self._candles(
            f"/historical/markets/{ticker}/candlesticks", start_ts, end_ts, period_interval
        )

    async def candlesticks(
        self, series_ticker: str, ticker: str, *, start_ts: int, end_ts: int, period_interval: int
    ) -> list[Candlestick]:
        """Candlesticks of a live market; needs the market's series ticker."""
        return await self._candles(
            f"/series/{series_ticker}/markets/{ticker}/candlesticks",
            start_ts,
            end_ts,
            period_interval,
        )
