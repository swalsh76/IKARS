"""What a stored market is, in one place for the downloader and the ingest.

The downloader's Parquet files and the database's ``market`` table hold the same row, so that a
history import is a copy: the same columns in the same order, built by :func:`market_values`. The
rules that go with it live here too: combo (``KXMVE``) markets are skipped everywhere (CLAUDE.md),
a market's series is the first part of its event ticker, and its outcome label (KAPS decision 23).
"""

import json
from typing import Any

from ikars_core.models import Market

COMBO_PREFIX = "KXMVE"
SETTLED_STATUSES = frozenset({"finalized", "settled", "determined"})
LABELS = ("yes", "no", "scalar", "other_settled", "unsettled")

MARKET_TEXT = (
    "ticker", "event_ticker", "series_ticker", "source", "label", "status", "result", "market_type",
    "title", "yes_sub_title", "no_sub_title", "rules_primary", "rules_secondary",
    "early_close_condition", "strike_type", "floor_strike", "cap_strike", "price_level_structure",
)  # fmt: skip
MARKET_FLAGS = ("exchange_index", "can_close_early")  # an integer and a boolean
MARKET_TIMES = (
    "created_time", "open_time", "close_time", "expected_expiration_time", "expiration_time",
    "latest_expiration_time", "occurrence_datetime", "settlement_ts", "updated_time",
)  # fmt: skip
MARKET_INTS = (
    "settlement_value_e6", "last_price_e6", "previous_price_e6", "yes_bid_e6", "yes_ask_e6",
    "no_bid_e6", "no_ask_e6", "yes_bid_size_e2", "yes_ask_size_e2", "volume_e2", "volume_24h_e2",
    "open_interest_e2", "notional_value_e6",
)  # fmt: skip
MARKET_COLUMNS = (*MARKET_TEXT, *MARKET_FLAGS, *MARKET_TIMES, *MARKET_INTS, "raw_json")


def is_combo(ticker: str) -> bool:
    return ticker.startswith(COMBO_PREFIX)


def series_ticker_of(event_ticker: str | None) -> str | None:
    """A market's series is the first part of its event ticker (checked live in K0.5.3b)."""
    return event_ticker.split("-")[0] if event_ticker else None


def label_of(market: Market) -> str:
    """The outcome label the study uses (KAPS decision 23).

    ``yes`` and ``no`` are the usable binary outcomes. ``scalar`` markets settle at a fraction
    of a dollar (the settlement value) and are kept apart. ``other_settled`` is anything with a
    settled status but no result, which the study leaves out until a study says otherwise.
    """
    if market.result in ("yes", "no", "scalar"):
        return market.result
    if market.status in SETTLED_STATUSES:
        return "other_settled"
    return "unsettled"


def market_values(market: Market, raw: dict[str, Any], source: str) -> tuple[Any, ...]:
    """One market as values in ``MARKET_COLUMNS`` order, with its original JSON."""
    derived = {
        "series_ticker": series_ticker_of(market.event_ticker),
        "source": source,
        "label": label_of(market),
        "raw_json": json.dumps(raw, separators=(",", ":")),
    }
    return tuple(
        derived[name] if name in derived else getattr(market, name) for name in MARKET_COLUMNS
    )
