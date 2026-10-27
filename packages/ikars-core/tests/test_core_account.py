"""The account models and reader, on hand-written responses with made-up values (docs/TESTING.md),
shaped as Kalshi's API reference documents them (read 2026-10-26)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ikars_core.account import (
    Account,
    AccountLimits,
    Balance,
    Fill,
    InstanceTransfer,
    MarketPosition,
    Settlement,
    Transfer,
)
from ikars_core.auth import AccountSigner
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import ParameterError
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import KalshiAccountClient, KalshiResponseError
from pydantic import ValidationError

FILL = {
    "fill_id": "f-1",
    "trade_id": "f-1",
    "order_id": "o-1",
    "ticker": "KXDEMO-26JAN01-T50",
    "exchange_index": 0,
    "outcome_side": "yes",
    "book_side": "bid",
    "count_fp": "10.00",
    "yes_price_dollars": "0.5600",
    "no_price_dollars": "0.4400",
    "is_taker": True,
    "created_time": "2026-01-01T12:00:00Z",
    "ts": 1767268800,
    "fee_cost": "0.1800",
    "subaccount_number": 0,
}
SETTLEMENT = {
    "ticker": "KXDEMO-26JAN01-T50",
    "event_ticker": "KXDEMO-26JAN01",
    "exchange_index": 0,
    "market_result": "yes",
    "yes_count_fp": "10.00",
    "yes_total_cost_dollars": "5.6000",
    "no_count_fp": "0.00",
    "no_total_cost_dollars": "0.0000",
    "revenue": 1000,
    "settled_time": "2026-01-02T00:00:00Z",
    "fee_cost": "0.1800",
    "value": 100,
}
POSITION = {
    "ticker": "KXDEMO-26JAN01-T50",
    "exchange_index": 0,
    "total_traded_dollars": "4.4000",
    "position_fp": "-10.00",
    "market_exposure_dollars": "4.4000",
    "realized_pnl_dollars": "-0.2500",
    "fees_paid_dollars": "0.1800",
    "last_updated_ts": "2026-01-01T12:00:00Z",
}
EVENT_POSITION = {
    "event_ticker": "KXDEMO-26JAN01",
    "total_cost_dollars": "4.4000",
    "total_cost_shares_fp": "10.00",
    "event_exposure_dollars": "4.4000",
    "realized_pnl_dollars": "0.0000",
    "fees_paid_dollars": "0.1800",
}
DEPOSIT = {
    "id": "d-1",
    "status": "applied",
    "type": "ach",
    "amount_cents": 50000,
    "fee_cents": 0,
    "created_ts": 1767225600,
    "finalized_ts": 1767312000,
}


# --- models -------------------------------------------------------------------------------------


def test_a_fill_is_read_in_fixed_point() -> None:
    fill = Fill.model_validate(FILL)
    assert (fill.count_e2, fill.yes_price_e6, fill.no_price_e6, fill.fee_e6) == (
        1000,
        560_000,
        440_000,
        180_000,
    )
    assert fill.is_taker and fill.outcome_side == "yes" and fill.created_time.year == 2026


def test_a_fill_named_only_by_its_legacy_trade_id_is_read() -> None:
    legacy = {k: v for k, v in FILL.items() if k != "fill_id"}
    assert Fill.model_validate(legacy).fill_id == "f-1"


def test_a_fill_whose_prices_do_not_add_to_a_dollar_is_refused() -> None:
    with pytest.raises(ValidationError, match="add to"):
        Fill.model_validate({**FILL, "no_price_dollars": "0.4500"})


def test_a_price_with_more_than_six_places_is_refused_not_rounded() -> None:
    with pytest.raises(ValidationError):
        Fill.model_validate({**FILL, "fee_cost": "0.1800001"})


def test_a_settlement_converts_cents_to_millionths() -> None:
    s = Settlement.model_validate(SETTLEMENT)
    assert s.revenue_e6 == 10_000_000 and s.value_e6 == 1_000_000
    assert s.yes_total_cost_e6 == 5_600_000 and s.fee_e6 == 180_000


def test_cents_must_be_whole_numbers() -> None:
    for bad in ("1000", 10.5, True):
        with pytest.raises(ValidationError, match="whole cents"):
            Settlement.model_validate({**SETTLEMENT, "revenue": bad})


def test_a_no_position_is_negative() -> None:
    p = MarketPosition.model_validate(POSITION)
    assert p.position_e2 == -1000 and p.realized_pnl_e6 == -250_000


def test_a_deposit_is_in_millionths() -> None:
    d = Transfer.model_validate(DEPOSIT)
    assert (d.amount_e6, d.fee_e6, d.finalized_ts) == (500_000_000, 0, 1767312000)


def test_an_instance_transfer_is_read() -> None:
    t = InstanceTransfer.model_validate(
        {
            "transfer_id": "t-1",
            "source": "event_contract",
            "destination": "margined",
            "source_exchange_shard": 0,
            "destination_exchange_shard": 1,
            "amount": "25.5000",
            "status": "complete",
            "created_ts": 1767225600,
        }
    )
    assert t.amount_e6 == 25_500_000 and t.client_transfer_id is None


BALANCE = {
    "balance": 12345,
    "balance_dollars": "123.4500",
    "portfolio_value": 2000,
    "updated_ts": 1,
}


def test_a_balance_is_read_and_its_two_forms_must_agree() -> None:
    b = Balance.model_validate(BALANCE)
    assert (b.balance_e6, b.balance_dollars_e6, b.portfolio_value_e6) == (
        123_450_000,
        123_450_000,
        20_000_000,
    )
    assert Balance.model_validate({**BALANCE, "balance_dollars": "123.455"}).balance_e6
    with pytest.raises(ValidationError, match="disagree"):
        Balance.model_validate({**BALANCE, "balance_dollars": "123.4600"})


def test_account_limits_are_read() -> None:
    limits = AccountLimits.model_validate(
        {
            "usage_tier": "basic",
            "read": {"refill_rate": 20, "bucket_capacity": 40},
            "write": {"refill_rate": 10, "bucket_capacity": 10},
            "grants": [
                {"exchange_instance": "event_contract", "level": "basic", "source": "volume"}
            ],
        }
    )
    assert limits.read.refill_rate == 20 and limits.grants[0].expires_ts is None


# --- the reader ---------------------------------------------------------------------------------


def reader(
    routes: Callable[[httpx.Request], dict[str, Any]],
) -> tuple[Account, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=json.dumps(routes(request)).encode())

    client = KalshiAccountClient(
        KalshiConfig(environment=Environment.PRODUCTION),
        AccountSigner("k", Ed25519PrivateKey.from_private_bytes(bytes(32))),
        bucket=TokenBucket(rate=1e9, capacity=1e9),
        transport=httpx.MockTransport(handle),
    )
    return Account(client), seen


async def test_balance_and_limits_are_read() -> None:
    limits = {"usage_tier": "basic", "read": {"refill_rate": 1, "bucket_capacity": 1}}
    limits["write"] = limits["read"]
    account, seen = reader(lambda r: BALANCE if r.url.path.endswith("balance") else limits)
    assert (await account.balance()).balance_e6 == 123_450_000
    assert (await account.limits()).usage_tier == "basic"
    assert [r.url.path for r in seen] == [
        "/trade-api/v2/portfolio/balance",
        "/trade-api/v2/account/limits",
    ]


async def test_positions_gather_every_page() -> None:
    pages = {
        None: {"market_positions": [POSITION], "event_positions": [], "cursor": "c2"},
        "c2": {"market_positions": [], "event_positions": [EVENT_POSITION], "cursor": ""},
    }
    account, seen = reader(lambda r: pages[r.url.params.get("cursor")])
    positions = await account.positions(settlement_status="all")
    assert len(positions.market_positions) == 1 and len(positions.event_positions) == 1
    assert seen[0].url.params["settlement_status"] == "all"


async def test_resting_orders_are_read_in_fixed_point() -> None:
    """M1.R 8: the reconciliation needs the cost of resting buys (made-up order)."""
    order = {"order_id": "o-1", "ticker": "KXT-26JAN01-A", "status": "resting", "action": "buy",
             "outcome_side": "no", "yes_price_dollars": "0.3000", "no_price_dollars": "0.7000",
             "remaining_count_fp": "12.50", "client_order_id": "x"}  # fmt: skip
    account, seen = reader(lambda _: {"orders": [order], "cursor": ""})
    pages = [p async for p in account.orders()]
    read = pages[0].items[0]
    assert (read.no_price_e6, read.remaining_e2, read.outcome_side) == (700_000, 1_250, "no")
    assert seen[0].url.path == "/trade-api/v2/portfolio/orders"
    assert seen[0].url.params["status"] == "resting"
    with pytest.raises(ValueError):
        account.orders(status="open")


async def test_historical_positions_use_the_archive_route_without_a_status() -> None:
    account, seen = reader(lambda _: {"market_positions": [], "event_positions": []})
    await account.positions(historical=True)
    assert seen[0].url.path == "/trade-api/v2/historical/positions"
    assert "settlement_status" not in seen[0].url.params


async def test_a_repeated_positions_cursor_is_an_error() -> None:
    account, _ = reader(lambda _: {"market_positions": [], "event_positions": [], "cursor": "x"})
    with pytest.raises(KalshiResponseError, match="cursor repeated"):
        await account.positions()


async def test_an_unknown_settlement_status_is_refused_before_sending() -> None:
    account, seen = reader(lambda _: {})
    with pytest.raises(ParameterError):
        await account.positions(settlement_status="open")
    assert seen == []


async def test_fills_page_with_their_parameters() -> None:
    account, seen = reader(lambda _: {"fills": [FILL], "cursor": ""})
    pages = [p async for p in account.fills(min_ts=10, max_ts=20, historical=True, limit=50)]
    assert pages[0].items[0].fill_id == "f-1"
    params = seen[0].url.params
    assert seen[0].url.path == "/trade-api/v2/historical/fills"
    assert (params["min_ts"], params["max_ts"], params["limit"]) == ("10", "20", "50")


async def test_settlements_deposits_withdrawals_and_transfers_page() -> None:
    bodies: dict[str, dict[str, Any]] = {
        "settlements": {"settlements": [SETTLEMENT]},
        "deposits": {"deposits": [DEPOSIT]},
        "withdrawals": {"withdrawals": [DEPOSIT]},
        "intra_exchange_instance_transfers": {"transfers": []},
    }
    account, _ = reader(lambda r: bodies[r.url.path.rsplit("/", 1)[1]])
    assert [p async for p in account.settlements()][0].items[0].market_result == "yes"
    assert [p async for p in account.deposits()][0].items[0].id == "d-1"
    assert [p async for p in account.withdrawals(limit=500)][0].items[0].type == "ach"
    assert [p async for p in account.instance_transfers()][0].items == []


def test_transfer_listings_page_at_most_500() -> None:
    account, _ = reader(lambda _: {})
    with pytest.raises(ParameterError, match="between 1 and 500"):
        account.deposits(limit=501)
    with pytest.raises(ParameterError):
        account.fills(min_ts=20, max_ts=10)


DIRECTIONLESS = {k: v for k, v in FILL.items() if k not in ("outcome_side", "book_side")}


@pytest.mark.parametrize(
    ("side", "action", "outcome", "book"),
    [("yes", "buy", "yes", "bid"), ("no", "sell", "yes", "bid"),
     ("no", "buy", "no", "ask"), ("yes", "sell", "no", "ask")],
)  # fmt: skip
def test_a_fill_takes_its_direction_from_the_legacy_fields(
    side: str, action: str, outcome: str, book: str
) -> None:
    fill = Fill.model_validate({**DIRECTIONLESS, "side": side, "action": action})
    assert (fill.outcome_side, fill.book_side) == (outcome, book)


def test_one_direction_field_gives_the_other() -> None:
    assert Fill.model_validate({**DIRECTIONLESS, "book_side": "ask"}).outcome_side == "no"
    assert Fill.model_validate({**DIRECTIONLESS, "outcome_side": "yes"}).book_side == "bid"


def test_a_fill_without_a_direction_is_refused() -> None:
    with pytest.raises(ValidationError, match="outcome_side"):
        Fill.model_validate(DIRECTIONLESS)


def test_a_fill_whose_directions_disagree_is_refused() -> None:
    with pytest.raises(ValidationError, match="disagree"):
        Fill.model_validate({**FILL, "outcome_side": "yes", "book_side": "ask"})


@pytest.mark.parametrize("bad", [2550, 25.5, True])
def test_a_dollar_amount_must_be_text(bad: object) -> None:
    transfer = {
        "transfer_id": "t",
        "source": "event_contract",
        "destination": "margined",
        "source_exchange_shard": 0,
        "destination_exchange_shard": 1,
        "amount": bad,
        "status": "complete",
        "created_ts": 1,
    }
    with pytest.raises(ValidationError, match="dollars as text"):
        InstanceTransfer.model_validate(transfer)
