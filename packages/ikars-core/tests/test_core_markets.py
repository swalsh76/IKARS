from datetime import UTC, datetime

import pytest
from ikars_core.markets import (
    COMBO_PREFIX,
    MARKET_COLUMNS,
    is_combo,
    label_of,
    market_values,
    series_ticker_of,
)
from ikars_core.models import Market


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"result": "yes", "status": "finalized"}, "yes"),
        ({"result": "no", "status": "finalized"}, "no"),
        ({"result": "scalar", "status": "finalized"}, "scalar"),
        ({"result": "", "status": "finalized"}, "other_settled"),
        ({"result": "", "status": "determined"}, "other_settled"),
        ({"result": "", "status": "active"}, "unsettled"),
        ({}, "unsettled"),
    ],
)
def test_labels(fields: dict[str, str], expected: str) -> None:
    assert label_of(Market.model_validate({"ticker": "KXA-1", **fields})) == expected


def test_combos_and_series_tickers() -> None:
    assert COMBO_PREFIX == "KXMVE"
    assert is_combo("KXMVECROSS-1")
    assert not is_combo("KXBTC-1")
    assert series_ticker_of("KXBTC15M-26OCT221315") == "KXBTC15M"
    assert series_ticker_of("") is None
    assert series_ticker_of(None) is None


def test_market_values_follow_the_columns_and_derive_four_of_them() -> None:
    market = Market.model_validate(
        {
            "ticker": "KXA-1-X",
            "event_ticker": "KXA-1",
            "status": "finalized",
            "result": "yes",
            "close_time": "2026-10-21T12:00:00Z",
            "last_price_dollars": "0.9900",
            "volume_fp": "12.00",
        }
    )
    values = dict(
        zip(MARKET_COLUMNS, market_values(market, {"ticker": "KXA-1-X"}, "historical"), strict=True)
    )
    assert len(MARKET_COLUMNS) == len(set(MARKET_COLUMNS))
    assert MARKET_COLUMNS[0] == "ticker" and MARKET_COLUMNS[-1] == "raw_json"
    assert (values["series_ticker"], values["source"], values["label"]) == (
        "KXA",
        "historical",
        "yes",
    )
    assert values["raw_json"] == '{"ticker":"KXA-1-X"}'
    assert values["close_time"] == datetime(2026, 10, 21, 12, tzinfo=UTC)
    assert (values["last_price_e6"], values["volume_e2"]) == (990_000, 1_200)
    assert values["settlement_ts"] is None
