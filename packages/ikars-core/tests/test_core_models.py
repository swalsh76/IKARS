from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from ikars_core.models import (
    Candlestick,
    Event,
    ExchangeSchedule,
    ExchangeStatus,
    HistoricalCutoff,
    Market,
    Series,
    SeriesFeeChange,
    Trade,
)
from pydantic import ValidationError

Sample = Callable[[str], dict[str, Any]]


def test_a_settled_market(sample: Sample) -> None:
    market = Market.model_validate(sample("market_settled.json"))
    assert market.ticker == "KXDEMO-26JAN01-T50"
    assert (market.status, market.result) == ("finalized", "yes")
    assert market.settlement_value_e6 == 1_000_000
    assert market.settlement_ts == datetime(2026, 1, 1, 18, 0, 5, 82510, tzinfo=UTC)
    assert market.last_price_e6 == 620_000
    assert market.yes_bid_size_e2 == 12_000
    assert market.yes_ask_size_e2 == 8_050
    assert market.volume_e2 == 553_807
    assert market.open_interest_e2 == 120_425
    assert market.floor_strike == "50.5"  # a level, kept as text
    assert market.price_level_structure == "linear_cent"
    assert market.rules_secondary is None  # blank means absent


def test_an_open_market_has_no_result_and_no_settlement(sample: Sample) -> None:
    market = Market.model_validate(sample("market_open.json"))
    assert market.result is None
    assert market.settlement_ts is None
    assert market.settlement_value_e6 is None
    assert market.floor_strike == "3.5"


def test_an_integer_strike_is_kept_as_text(sample: Sample) -> None:
    raw = sample("market_settled.json")
    assert Market.model_validate(raw | {"floor_strike": 4}).floor_strike == "4"
    assert Market.model_validate(raw | {"floor_strike": None}).floor_strike is None


def test_unknown_fields_are_ignored(sample: Sample) -> None:
    market = Market.model_validate(sample("market_settled.json"))
    assert not hasattr(market, "some_future_field")


def test_models_are_frozen(sample: Sample) -> None:
    market = Market.model_validate(sample("market_settled.json"))
    with pytest.raises(ValidationError):
        market.ticker = "OTHER"


def test_a_market_needs_a_ticker(sample: Sample) -> None:
    raw = sample("market_settled.json")
    del raw["ticker"]
    with pytest.raises(ValidationError):
        Market.model_validate(raw)


def test_five_decimal_places_are_exact_and_accepted(sample: Sample) -> None:
    raw = sample("market_settled.json") | {"last_price_dollars": "0.62001"}
    assert Market.model_validate(raw).last_price_e6 == 620_010


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("last_price_dollars", "0.6200001"),  # finer than a millionth
        ("volume_fp", "10.001"),  # finer than a hundredth of a contract
        ("yes_bid_dollars", "abc"),
        ("yes_bid_dollars", True),
        ("volume_fp", True),
        ("close_time", "yesterday"),
        ("close_time", "2026-01-01T17:00:00"),  # no time zone
    ],
)
def test_bad_values_are_refused_not_rounded(sample: Sample, field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        Market.model_validate(sample("market_settled.json") | {field: value})


def test_a_trade(sample: Sample) -> None:
    trade = Trade.model_validate(sample("trade.json"))
    assert trade.yes_price_e6 == 220_000
    assert trade.no_price_e6 == 780_000
    assert trade.count_e2 == 1_250
    assert trade.created_time == datetime(2026, 1, 1, 12, 0, 1, 123456, tzinfo=UTC)
    assert (trade.taker_side, trade.taker_book_side, trade.is_block_trade) == ("yes", "bid", False)


def test_the_taker_side_falls_back_to_the_deprecated_field(sample: Sample) -> None:
    raw = sample("trade.json")
    del raw["taker_outcome_side"]
    assert Trade.model_validate(raw).taker_side == "yes"
    del raw["taker_side"]
    assert Trade.model_validate(raw).taker_side is None


def test_yes_and_no_prices_must_add_to_a_dollar(sample: Sample) -> None:
    with pytest.raises(ValidationError, match="add to"):
        Trade.model_validate(sample("trade.json") | {"no_price_dollars": "0.7700"})


@pytest.mark.parametrize(
    "missing", ["trade_id", "ticker", "created_time", "count_fp", "yes_price_dollars"]
)
def test_a_trade_needs_its_identity_and_numbers(sample: Sample, missing: str) -> None:
    raw = sample("trade.json")
    del raw[missing]
    with pytest.raises(ValidationError):
        Trade.model_validate(raw)


def test_a_trade_with_extra_precision_is_refused(sample: Sample) -> None:
    with pytest.raises(ValidationError):
        Trade.model_validate(sample("trade.json") | {"count_fp": "1.001"})


def test_something_that_is_not_an_object_is_refused() -> None:
    for model in (Market, Trade, Candlestick):
        with pytest.raises(ValidationError):
            model.model_validate("text")


def test_the_two_candlestick_shapes_read_into_one(sample: Sample) -> None:
    historical = Candlestick.model_validate(sample("candle_historical.json"))
    live = Candlestick.model_validate(sample("candle_live.json"))
    assert historical == live
    assert historical.end_period == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert historical.price.open_e6 == 30_000
    assert historical.price.high_e6 == 750_000
    assert historical.price.mean_e6 == 30_000
    assert historical.price.previous_e6 == 30_000
    assert historical.yes_bid.close_e6 == 0
    assert historical.yes_ask.high_e6 == 750_000
    assert historical.volume_e2 == 458_945
    assert historical.open_interest_e2 == 387_470


def test_a_candle_with_no_trades_has_an_empty_price_band(sample: Sample) -> None:
    candle = Candlestick.model_validate(sample("candle_live_empty.json"))
    assert candle.price.open_e6 is None
    assert candle.price.close_e6 is None
    assert candle.yes_ask.close_e6 == 990_000
    assert candle.volume_e2 == 0


def test_a_candle_with_missing_blocks_still_reads() -> None:
    candle = Candlestick.model_validate({"end_period_ts": 1767268800})
    assert candle.price.open_e6 is None
    assert candle.volume_e2 is None


def test_a_candle_needs_its_period_end() -> None:
    with pytest.raises(ValidationError):
        Candlestick.model_validate({"volume": "1.00"})
    with pytest.raises(ValidationError):
        Candlestick.model_validate({"end_period_ts": True})


def test_a_series_reads_a_float_multiplier_exactly(sample: Sample) -> None:
    series = Series.model_validate(sample("series.json"))
    assert series.fee_multiplier_e6 == 500_000
    assert series.fee_type == "quadratic_with_maker_fees"
    assert (series.category, series.frequency) == ("Crypto", "daily")
    assert series.tags == ["Crypto", "Demo"]


def test_a_series_with_null_tags_has_no_tags(sample: Sample) -> None:
    # 2,617 of 15,336 real series send "tags": null (found by the live check)
    assert Series.model_validate(sample("series.json") | {"tags": None}).tags == []
    without = {k: v for k, v in sample("series.json").items() if k != "tags"}
    assert Series.model_validate(without).tags == []


@pytest.mark.parametrize(
    ("raw", "expected"), [(1, 1_000_000), ("1", 1_000_000), ("0.5", 500_000), ("0", 0)]
)
def test_multipliers_in_every_form_the_api_uses(sample: Sample, raw: Any, expected: int) -> None:
    series = Series.model_validate(sample("series.json") | {"fee_multiplier": raw})
    assert series.fee_multiplier_e6 == expected
    change = SeriesFeeChange.model_validate(sample("fee_change.json") | {"fee_multiplier": raw})
    assert change.fee_multiplier_e6 == expected


def test_a_fee_change(sample: Sample) -> None:
    change = SeriesFeeChange.model_validate(sample("fee_change.json"))
    assert change.series_ticker == "KXDEMO"
    assert change.scheduled_ts == datetime(2026, 9, 3, 18, 35, 23, tzinfo=UTC)
    incomplete = {k: v for k, v in sample("fee_change.json").items() if k != "fee_type"}
    with pytest.raises(ValidationError):
        SeriesFeeChange.model_validate(incomplete)
    no_multiplier = {k: v for k, v in sample("fee_change.json").items() if k != "fee_multiplier"}
    with pytest.raises(ValidationError):
        SeriesFeeChange.model_validate(no_multiplier)


def test_an_event(sample: Sample) -> None:
    event = Event.model_validate(sample("event.json"))
    assert (event.series_ticker, event.category) == ("KXDEMO", "Crypto")
    assert event.mutually_exclusive is False
    assert event.strike_period is None


def test_the_exchange_status_with_its_shards(sample: Sample) -> None:
    status = ExchangeStatus.model_validate(sample("exchange_status.json"))
    assert status.exchange_active
    assert status.trading_active
    assert [(s.exchange_index, s.description, s.trading_active) for s in status.shards] == [
        (0, "Default", True),
        (1, "Combos", False),
    ]


def test_the_exchange_schedule_shows_the_thursday_closure(sample: Sample) -> None:
    schedule = ExchangeSchedule.model_validate(sample("exchange_schedule.json"))
    (hours,) = schedule.standard_hours
    assert [(s.open_time, s.close_time) for s in hours.thursday] == [
        ("00:00", "03:00"),
        ("05:00", "00:00"),
    ]
    assert len(hours.monday) == 1
    assert schedule.maintenance_windows == []
    assert hours.end_time.year == 2200


def test_the_historical_cutoff(sample: Sample) -> None:
    cutoff = HistoricalCutoff.model_validate(sample("cutoff.json"))
    assert cutoff.market_settled_ts == datetime(2026, 8, 22, tzinfo=UTC)
    assert cutoff.trades_created_ts == datetime(2026, 8, 22, tzinfo=UTC)
    assert cutoff.orders_updated_ts == datetime(2026, 10, 7, tzinfo=UTC)
    without = {
        k: v for k, v in sample("cutoff.json").items() if k != "market_positions_last_updated_ts"
    }
    assert HistoricalCutoff.model_validate(without).market_positions_last_updated_ts is None
    with pytest.raises(ValidationError):
        HistoricalCutoff.model_validate({"trades_created_ts": "2026-08-22T00:00:00Z"})
