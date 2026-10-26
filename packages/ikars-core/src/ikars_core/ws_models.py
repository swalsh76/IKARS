"""Typed models for Kalshi's market-data WebSocket channels: ticker, trade, the lifecycle channels
and the order book (``orderbook_delta``: a snapshot, then deltas).

Money and counts become fixed-point integers on the way in (a value more precise than the scale
is refused, never rounded), exactly as for the REST models. A frame is parsed once, here, with
floats kept as text, so a price such as ``0.56`` never exists as a binary float.

Two choices differ from a lenient reader:

* A field Kalshi adds is **counted**, not silently ignored: ``Frame.unknown`` lists it, and the
  feed keeps a running total per message type for ``status``, so a change of schema is noticed.
* A message that does not fit its model is **kept**, not dropped: ``Frame.error`` says why and
  ``Frame.raw`` still holds what arrived, so nothing recorded is lost to a parser bug.

Only the market-data channels live here. The order-book channel needs a signed connection but
carries market data, not account data. The private channels (fills, orders, positions) belong to
the trading client (M7); the data key never subscribes to them.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from ikars_core.fixedpoint import to_e2, to_e6
from ikars_core.models import (
    Model,
    Moment,
    OptCountE2,
    OptDollarsE6,
    RequiredCountE2,
    RequiredDollarsE6,
    Text,
    check_prices_add_to_a_dollar,
)

TICKER = "ticker"
TRADE = "trade"
MARKET_LIFECYCLE = "market_lifecycle_v2"
EVENT_LIFECYCLE = "event_lifecycle"
COMBO_LIFECYCLE = "multivariate_market_lifecycle"
ORDERBOOK_SNAPSHOT = "orderbook_snapshot"
ORDERBOOK_DELTA = "orderbook_delta"
ORDERBOOK_CHANNEL = "orderbook_delta"  # the channel name; it carries both message types
RESPONSE_TYPES = frozenset({"subscribed", "ok", "unsubscribed", "error"})


WsModel = Model  # the WebSocket messages share the REST models' base


class TickerMsg(WsModel):
    """The best prices and the totals of one market, sent whenever any of them changes."""

    market_ticker: str
    market_id: Text = None
    price_e6: OptDollarsE6 = Field(default=None, validation_alias="price_dollars")
    yes_bid_e6: OptDollarsE6 = Field(default=None, validation_alias="yes_bid_dollars")
    yes_ask_e6: OptDollarsE6 = Field(default=None, validation_alias="yes_ask_dollars")
    yes_bid_size_e2: OptCountE2 = Field(default=None, validation_alias="yes_bid_size_fp")
    yes_ask_size_e2: OptCountE2 = Field(default=None, validation_alias="yes_ask_size_fp")
    volume_e2: OptCountE2 = Field(default=None, validation_alias="volume_fp")
    open_interest_e2: OptCountE2 = Field(default=None, validation_alias="open_interest_fp")
    last_trade_size_e2: OptCountE2 = Field(default=None, validation_alias="last_trade_size_fp")
    dollar_volume_e6: OptDollarsE6 = Field(default=None, validation_alias="dollar_volume")
    dollar_open_interest_e6: OptDollarsE6 = Field(
        default=None, validation_alias="dollar_open_interest"
    )  # both arrive as whole dollars
    ts_ms: int | None = None  # when it happened, in milliseconds
    ts: int | None = None  # the same moment in whole seconds
    time: Moment = None  # the same moment as an RFC 3339 text


class TradeMsg(WsModel):
    """One trade, as it happened."""

    trade_id: str
    market_ticker: str
    yes_price_e6: RequiredDollarsE6 = Field(validation_alias="yes_price_dollars")
    no_price_e6: RequiredDollarsE6 = Field(validation_alias="no_price_dollars")
    count_e2: RequiredCountE2 = Field(validation_alias="count_fp")
    taker_side: Text = None  # yes or no; always the same as taker_outcome_side on the live feed
    taker_outcome_side: Text = None  # yes or no: the taker's outcome side
    taker_book_side: Text = None  # bid (a yes taker) or ask (a no taker)
    is_block_trade: bool = False
    ts_ms: int | None = None
    ts: int | None = None  # the same moment in whole seconds

    @model_validator(mode="after")
    def _prices_add_to_a_dollar(self) -> "TradeMsg":
        check_prices_add_to_a_dollar(self.yes_price_e6, self.no_price_e6)
        return self


class MarketLifecycleMsg(WsModel):
    """A market was created, opened, closed, determined or settled, or its terms changed.

    The times arrive as whole seconds since the epoch, which pydantic reads as UTC times.
    """

    market_ticker: Text = None
    event_type: str
    open_ts: Moment = None
    close_ts: Moment = None
    determination_ts: Moment = None
    settled_ts: Moment = None
    result: Text = None
    settlement_value_e6: OptDollarsE6 = Field(default=None, validation_alias="settlement_value")
    is_deactivated: bool | None = None
    exchange_index: int | None = None
    price_level_structure: Text = None
    additional_metadata: dict[str, Any] | None = None
    price_ranges: list[dict[str, Any]] | None = None  # the tick structure, on creation


class EventLifecycleMsg(WsModel):
    """An event was created (sent on both lifecycle channels)."""

    event_ticker: str
    series_ticker: Text = None
    title: str = ""
    subtitle: str = ""
    collateral_return_type: str = ""
    exchange_index: int | None = None


Level = tuple[int, int]
"""One price level: (price in dollar millionths, contracts in hundredths)."""


def _levels(value: Any) -> Any:
    """``[[price_dollars, count_fp], ...]`` as fixed-point pairs; absent means an empty side."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("a book side must be a list of [price, count] pairs")
    levels: list[Level] = []
    for entry in value:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError(f"a level must be a [price, count] pair, got {entry!r}")
        price, count = entry
        if not isinstance(price, str) or not isinstance(count, str):
            raise ValueError(f"a level's price and count must be text, got {entry!r}")
        levels.append((to_e6(price), to_e2(count)))
    return tuple(levels)


BookSide = Annotated[tuple[Level, ...], BeforeValidator(_levels)]


class OrderbookSnapshotMsg(WsModel):
    """The whole book of one market: every change with a clock at or below ``clock`` is included.
    Each side is that side's **bids** (a YES ask is $1 minus the best NO bid)."""

    market_ticker: str
    market_id: Text = None
    clock: int = 0
    yes: BookSide = Field(default=(), validation_alias="yes_dollars_fp")
    no: BookSide = Field(default=(), validation_alias="no_dollars_fp")


class OrderbookDeltaMsg(WsModel):
    """One change to one price level of one side; several may share one ``clock``."""

    market_ticker: str
    market_id: Text = None
    clock: int
    price_e6: RequiredDollarsE6 = Field(validation_alias="price_dollars")
    delta_e2: RequiredCountE2 = Field(validation_alias="delta_fp")  # signed
    side: Literal["yes", "no"]
    client_order_id: Text = None  # only when one's own order made the change
    ts_ms: int | None = None
    subaccount: int | None = None
    ts: Text = None  # deprecated: ts_ms is the same time


Payload = (
    TickerMsg
    | TradeMsg
    | MarketLifecycleMsg
    | EventLifecycleMsg
    | OrderbookSnapshotMsg
    | OrderbookDeltaMsg
)
MODELS: dict[str, type[WsModel]] = {
    TICKER: TickerMsg,
    TRADE: TradeMsg,
    MARKET_LIFECYCLE: MarketLifecycleMsg,
    COMBO_LIFECYCLE: MarketLifecycleMsg,
    EVENT_LIFECYCLE: EventLifecycleMsg,
    ORDERBOOK_SNAPSHOT: OrderbookSnapshotMsg,
    ORDERBOOK_DELTA: OrderbookDeltaMsg,
}


def known_keys(model: type[WsModel]) -> frozenset[str]:
    """Every name under which the model reads a field: its alias if it has one, else its name."""
    names: set[str] = set()
    for name, field in model.model_fields.items():
        alias = field.validation_alias
        names.add(alias if isinstance(alias, str) else name)
        names.add(name)
    return frozenset(names)


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame off the socket, parsed once."""

    type: str
    sid: int | None
    seq: int | None
    id: int | None
    sending_ts_ms: int | None
    payload: Payload | None  # the typed message, for a known data type that parsed
    raw: Mapping[str, Any]  # the frame as received (floats as text)
    unknown: tuple[str, ...] = ()  # fields in the message that the model does not know
    error: str | None = None  # why a known data type did not parse, or why the frame is odd

    @property
    def is_response(self) -> bool:
        return self.type in RESPONSE_TYPES


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_frame(raw: str | bytes) -> Frame:
    """Parse one frame. Raises ``ValueError`` only when it is not a JSON object with a ``type``."""
    document = json.loads(raw, parse_float=str)
    if not isinstance(document, dict) or not isinstance(document.get("type"), str):
        raise ValueError("a frame must be a JSON object with a text 'type'")
    kind = document["type"]
    message = document.get("msg")
    payload: Payload | None = None
    unknown: tuple[str, ...] = ()
    error: str | None = None
    model = MODELS.get(kind)
    if model is not None:
        if isinstance(message, dict):
            unknown = tuple(sorted(set(message) - known_keys(model)))
            try:
                payload = model.model_validate(message)  # type: ignore[assignment]
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first["loc"])
                error = f"{kind}: {where}: {first['msg']}"[:300]
        else:
            error = f"{kind}: the message is not an object"
    return Frame(
        type=kind,
        sid=_int_or_none(document.get("sid")),
        seq=_int_or_none(document.get("seq")),
        id=_int_or_none(document.get("id")),
        sending_ts_ms=_int_or_none(document.get("sending_ts_ms")),
        payload=payload,
        raw=document,
        unknown=unknown,
        error=error,
    )


__all__ = [
    "COMBO_LIFECYCLE",
    "EVENT_LIFECYCLE",
    "Frame",
    "MARKET_LIFECYCLE",
    "ORDERBOOK_CHANNEL",
    "ORDERBOOK_DELTA",
    "ORDERBOOK_SNAPSHOT",
    "RESPONSE_TYPES",
    "TICKER",
    "TRADE",
    "EventLifecycleMsg",
    "Level",
    "MarketLifecycleMsg",
    "OrderbookDeltaMsg",
    "OrderbookSnapshotMsg",
    "Payload",
    "TickerMsg",
    "TradeMsg",
    "known_keys",
    "parse_frame",
]
