"""Typed models for Kalshi's public market-data responses.

Money and counts are converted to fixed-point integers by :mod:`ikars_core.fixedpoint` (a value
with more precision than the scale is refused, never rounded) and times become timezone-aware
datetimes. Unknown fields are ignored so that a field Kalshi adds cannot break a long download;
statuses, sides and types stay plain text for the same reason. Fields that are not needed by the
analytics are not modelled; the downloader keeps a compressed copy of each market's original
JSON for anything missed.

The client reads JSON with floats as text, so a number such as ``0.5`` reaches these models as
``"0.5"`` and an integer as an ``int``.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ikars_core.fixedpoint import E6, to_e2, to_e6


def _blank_is_none(value: Any) -> Any:
    return None if value == "" else value


def _fixed(convert: Any) -> Any:
    def parse(value: Any) -> int | None:
        value = _blank_is_none(value)
        if value is None:
            return None
        try:
            return int(convert(value))
        except TypeError as exc:  # a bool or float: not a value we can trust to be exact
            raise ValueError(str(exc)) from None

    return parse


def _unix(value: Any) -> Any:
    if isinstance(value, int) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    return _blank_is_none(value)


OptDollarsE6 = Annotated[int | None, BeforeValidator(_fixed(to_e6))]
"""Dollars in millionths, or None when the field is absent or blank."""
OptCountE2 = Annotated[int | None, BeforeValidator(_fixed(to_e2))]
"""Contracts in hundredths, or None."""
RequiredDollarsE6 = Annotated[int, BeforeValidator(_fixed(to_e6))]
"""Dollars in millionths; the field is required."""
RequiredCountE2 = Annotated[int, BeforeValidator(_fixed(to_e2))]
"""Contracts in hundredths; the field is required."""
Moment = Annotated[AwareDatetime | None, BeforeValidator(_blank_is_none)]
"""A timezone-aware time, or None when absent or blank."""
Text = Annotated[str | None, BeforeValidator(_blank_is_none)]
"""Text, with a blank string meaning absent."""


class Model(BaseModel):
    """Every Kalshi model, REST and WebSocket: frozen, and blind to fields it does not know."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)


def check_prices_add_to_a_dollar(yes_e6: int, no_e6: int) -> None:
    """A trade's yes and no prices must add to exactly one dollar."""
    if yes_e6 + no_e6 != E6:
        raise ValueError("yes and no prices do not add to $1")


class Market(Model):
    ticker: str
    event_ticker: Text = None
    market_type: Text = None
    status: Text = None
    result: Text = None  # yes, no, scalar; None until determined
    title: Text = None
    yes_sub_title: Text = None
    no_sub_title: Text = None
    rules_primary: Text = None
    rules_secondary: Text = None
    early_close_condition: Text = None
    can_close_early: bool | None = None
    strike_type: Text = None
    floor_strike: Text = None  # kept as text: it is a level, not a price, and may be any precision
    cap_strike: Text = None
    price_level_structure: Text = None  # the tick structure, e.g. linear_cent
    exchange_index: int | None = None
    created_time: Moment = None
    open_time: Moment = None
    close_time: Moment = None
    expected_expiration_time: Moment = None
    expiration_time: Moment = None
    latest_expiration_time: Moment = None
    occurrence_datetime: Moment = None
    settlement_ts: Moment = None
    updated_time: Moment = None
    settlement_value_e6: OptDollarsE6 = Field(
        default=None, validation_alias="settlement_value_dollars"
    )
    last_price_e6: OptDollarsE6 = Field(default=None, validation_alias="last_price_dollars")
    previous_price_e6: OptDollarsE6 = Field(default=None, validation_alias="previous_price_dollars")
    yes_bid_e6: OptDollarsE6 = Field(default=None, validation_alias="yes_bid_dollars")
    yes_ask_e6: OptDollarsE6 = Field(default=None, validation_alias="yes_ask_dollars")
    no_bid_e6: OptDollarsE6 = Field(default=None, validation_alias="no_bid_dollars")
    no_ask_e6: OptDollarsE6 = Field(default=None, validation_alias="no_ask_dollars")
    yes_bid_size_e2: OptCountE2 = Field(default=None, validation_alias="yes_bid_size_fp")
    yes_ask_size_e2: OptCountE2 = Field(default=None, validation_alias="yes_ask_size_fp")
    volume_e2: OptCountE2 = Field(default=None, validation_alias="volume_fp")
    volume_24h_e2: OptCountE2 = Field(default=None, validation_alias="volume_24h_fp")
    open_interest_e2: OptCountE2 = Field(default=None, validation_alias="open_interest_fp")
    notional_value_e6: OptDollarsE6 = Field(default=None, validation_alias="notional_value_dollars")

    @model_validator(mode="before")
    @classmethod
    def _strikes_as_text(cls, data: Any) -> Any:
        # strike levels arrive as numbers (a float is already text here) or, for some types, text
        if isinstance(data, dict):
            data = dict(data)
            for key in ("floor_strike", "cap_strike"):
                if data.get(key) is not None:
                    data[key] = str(data[key])
        return data


class Trade(Model):
    trade_id: str
    ticker: str
    created_time: AwareDatetime
    yes_price_e6: RequiredDollarsE6 = Field(validation_alias="yes_price_dollars")
    no_price_e6: RequiredDollarsE6 = Field(validation_alias="no_price_dollars")
    count_e2: RequiredCountE2 = Field(validation_alias="count_fp")
    taker_side: Text = None  # yes or no: the taker's outcome side
    taker_book_side: Text = None  # bid or ask
    is_block_trade: bool = False

    @model_validator(mode="before")
    @classmethod
    def _outcome_side(cls, data: Any) -> Any:
        # the deprecated ``taker_side`` is the same value as ``taker_outcome_side``
        if isinstance(data, dict):
            data = {**data, "taker_side": data.get("taker_outcome_side") or data.get("taker_side")}
        return data

    @model_validator(mode="after")
    def _prices_add_to_a_dollar(self) -> "Trade":
        check_prices_add_to_a_dollar(self.yes_price_e6, self.no_price_e6)
        return self


class PriceBand(Model):
    """Open, high, low and close of a price over one candlestick period."""

    open_e6: OptDollarsE6 = Field(default=None, validation_alias="open")
    high_e6: OptDollarsE6 = Field(default=None, validation_alias="high")
    low_e6: OptDollarsE6 = Field(default=None, validation_alias="low")
    close_e6: OptDollarsE6 = Field(default=None, validation_alias="close")


class TradePriceBand(PriceBand):
    """The traded-price band; empty (all None) when nothing traded in the period."""

    mean_e6: OptDollarsE6 = Field(default=None, validation_alias="mean")  # volume-weighted
    previous_e6: OptDollarsE6 = Field(default=None, validation_alias="previous")


def _plain_keys(block: Any) -> dict[str, Any]:
    # live candlesticks name their price keys ``open_dollars`` and so on
    return {k.removesuffix("_dollars"): v for k, v in (block or {}).items()}


class Candlestick(Model):
    """One candlestick, whichever endpoint it came from.

    The historical endpoint says ``volume`` and ``open_interest`` with plain price keys; the live
    one says ``volume_fp`` and ``open_interest_fp`` with ``*_dollars`` price keys. Both are read
    into this one shape.
    """

    end_period: AwareDatetime
    price: TradePriceBand = TradePriceBand()
    yes_bid: PriceBand = PriceBand()
    yes_ask: PriceBand = PriceBand()
    volume_e2: OptCountE2 = None
    open_interest_e2: OptCountE2 = None

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            "end_period": _unix(data.get("end_period_ts")),
            "price": _plain_keys(data.get("price")),
            "yes_bid": _plain_keys(data.get("yes_bid")),
            "yes_ask": _plain_keys(data.get("yes_ask")),
            "volume_e2": data.get("volume_fp", data.get("volume")),
            "open_interest_e2": data.get("open_interest_fp", data.get("open_interest")),
        }


class Series(Model):
    ticker: str
    title: Text = None
    category: Text = None
    frequency: Text = None
    tags: Annotated[
        list[str], BeforeValidator(lambda v: [] if v is None else v)
    ] = []  # null in 17% of series
    exchange_index: int | None = None
    fee_type: Text = None
    fee_multiplier_e6: OptDollarsE6 = Field(default=None, validation_alias="fee_multiplier")


class SeriesFeeChange(Model):
    id: str
    series_ticker: str
    fee_type: str
    fee_multiplier_e6: RequiredDollarsE6 = Field(validation_alias="fee_multiplier")
    scheduled_ts: AwareDatetime


class Event(Model):
    event_ticker: str
    series_ticker: Text = None
    category: Text = None
    title: Text = None
    sub_title: Text = None
    mutually_exclusive: bool | None = None
    strike_period: Text = None


class Shard(Model):
    exchange_index: int
    description: Text = None
    exchange_active: bool
    trading_active: bool
    intra_exchange_transfers_active: bool | None = None


class ExchangeStatus(Model):
    exchange_active: bool
    trading_active: bool
    intra_exchange_transfers_active: bool | None = None
    shards: list[Shard] = Field(default=[], validation_alias="exchange_index_statuses")


class Session(Model):
    open_time: str  # "HH:MM"; the schedule is in Eastern time
    close_time: str


class StandardHours(Model):
    start_time: AwareDatetime
    end_time: AwareDatetime
    monday: list[Session] = []
    tuesday: list[Session] = []
    wednesday: list[Session] = []
    thursday: list[Session] = []
    friday: list[Session] = []
    saturday: list[Session] = []
    sunday: list[Session] = []


class ExchangeSchedule(Model):
    standard_hours: list[StandardHours] = []
    # No maintenance window has been observed yet, so the shape of one is not modelled.
    maintenance_windows: list[dict[str, Any]] = []


class HistoricalCutoff(Model):
    """Where data moves from the live endpoints to ``/historical``."""

    market_settled_ts: AwareDatetime  # markets settled before this, and their candlesticks
    trades_created_ts: AwareDatetime  # fills before this
    orders_updated_ts: AwareDatetime
    market_positions_last_updated_ts: Moment = None
