"""Typed reads of the owner's own Kalshi account, for the audit and tax ledger (PLAN D18).

Every read goes through :class:`ikars_core.rest.KalshiAccountClient`, which signs with the account
reader and can reach only these routes. Money is fixed-point like everywhere else: Kalshi's dollar
strings become dollars in millionths, and the fields Kalshi gives in **cents** (``balance``,
``portfolio_value``, a settlement's ``revenue`` and ``value``, deposit and withdrawal amounts and
fees) are converted to millionths too, so every amount here has one unit. Counts are hundredths of a
contract; a position's count is negative for NO contracts.

Kalshi does not document the unit of some integer timestamps (``updated_ts``, ``created_ts``,
``finalized_ts``); they are kept as the integers Kalshi sent, and the live test records what they
are. Nothing here is logged: these are account values.
"""

from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BeforeValidator, Field, model_validator

from ikars_core.endpoints import (
    MAX_PAGE_SIZE,
    ParameterError,
    Reader,
    TypedPage,
    _choice,
    _limit,
    _range,
    _rows,
)
from ikars_core.models import (
    Model,
    RequiredCountE2,
    RequiredDollarsE6,
    Text,
    check_prices_add_to_a_dollar,
)
from ikars_core.rest import KalshiAccountClient

CENTS_TO_E6 = 10_000
SETTLEMENT_STATUSES = frozenset({"unsettled", "settled", "all"})
BUY_SELL = ("buy", "sell")
TRANSFER_PAGE_SIZE = 500  # deposits, withdrawals and transfers page at most 500


def _cents(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected whole cents, got {value!r}")
    return value * CENTS_TO_E6


def _dollar_text(value: Any) -> Any:
    # Kalshi sends dollars as text; a bare number could be cents (as on deposits) and would be read
    # as whole dollars, a 100-fold error in the ledger, so it is refused
    if not isinstance(value, str):
        raise ValueError(f"expected dollars as text, got {type(value).__name__}")
    return value


Dollars = Annotated[RequiredDollarsE6, BeforeValidator(_dollar_text)]
"""Dollars in millionths, from Kalshi's dollar text only."""
CentsAsE6 = Annotated[int, BeforeValidator(_cents)]
"""An amount Kalshi gives in cents, as dollars in millionths."""
OptCentsAsE6 = Annotated[int | None, BeforeValidator(_cents)]


ORDER_STATUSES = frozenset({"resting", "canceled", "executed"})


class Balance(Model):
    balance_e6: CentsAsE6 = Field(validation_alias="balance")  # available balance
    balance_dollars_e6: Dollars = Field(validation_alias="balance_dollars")
    portfolio_value_e6: CentsAsE6 = Field(validation_alias="portfolio_value")
    updated_ts: int  # Unix time; the unit is not documented

    @model_validator(mode="after")
    def _the_two_balances_agree(self) -> "Balance":
        # ``balance`` is whole cents and ``balance_dollars`` may carry more places; however Kalshi
        # rounds the cents, the two must be less than a cent apart
        if abs(self.balance_dollars_e6 - self.balance_e6) >= CENTS_TO_E6:
            raise ValueError("balance and balance_dollars disagree by a cent or more")
        return self


class MarketPosition(Model):
    ticker: str
    exchange_index: int
    position_e2: RequiredCountE2 = Field(validation_alias="position_fp")  # negative: NO contracts
    total_traded_e6: Dollars = Field(validation_alias="total_traded_dollars")
    market_exposure_e6: Dollars = Field(validation_alias="market_exposure_dollars")
    realized_pnl_e6: Dollars = Field(validation_alias="realized_pnl_dollars")
    fees_paid_e6: Dollars = Field(validation_alias="fees_paid_dollars")
    last_updated_ts: AwareDatetime


class EventPosition(Model):
    event_ticker: str
    total_cost_e6: Dollars = Field(validation_alias="total_cost_dollars")
    total_cost_shares_e2: RequiredCountE2 = Field(validation_alias="total_cost_shares_fp")
    event_exposure_e6: Dollars = Field(validation_alias="event_exposure_dollars")
    realized_pnl_e6: Dollars = Field(validation_alias="realized_pnl_dollars")
    fees_paid_e6: Dollars = Field(validation_alias="fees_paid_dollars")


class Order(Model):
    """One of the owner's orders (read only: IKARS places none before M7). ``outcome_side`` is the
    exposure the order adds, as on a fill; a resting buy holds its cost back from the available
    balance."""

    order_id: str
    ticker: str
    status: str
    action: Literal["buy", "sell"]
    outcome_side: Literal["yes", "no"]
    yes_price_e6: Dollars = Field(validation_alias="yes_price_dollars")
    no_price_e6: Dollars = Field(validation_alias="no_price_dollars")
    remaining_e2: RequiredCountE2 = Field(validation_alias="remaining_count_fp")

    @model_validator(mode="before")
    @classmethod
    def _legacy_side(cls, data: Any) -> Any:
        # the deprecated ``side`` gives the exposure when ``outcome_side`` is missing (as on a fill)
        if isinstance(data, dict) and not data.get("outcome_side"):
            side, action = data.get("side"), data.get("action")
            if side in ("yes", "no") and action in BUY_SELL:
                other = "no" if side == "yes" else "yes"
                data = {**data, "outcome_side": side if action == "buy" else other}
        return data


class Positions(Model):
    market_positions: list[MarketPosition]
    event_positions: list[EventPosition]


class Fill(Model):
    fill_id: str
    order_id: str
    ticker: str
    exchange_index: int | None = None
    # the direction, required: a fill without one could not be costed in the ledger
    outcome_side: Literal["yes", "no"]  # the exposure this fill adds (buy YES or sell NO: yes)
    book_side: Literal["bid", "ask"]  # the same direction on the YES book (bid: yes)
    count_e2: RequiredCountE2 = Field(validation_alias="count_fp")
    yes_price_e6: Dollars = Field(validation_alias="yes_price_dollars")
    no_price_e6: Dollars = Field(validation_alias="no_price_dollars")
    fee_e6: Dollars = Field(validation_alias="fee_cost")
    is_taker: bool
    created_time: AwareDatetime
    subaccount_number: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_names(cls, data: Any) -> Any:
        # ``trade_id`` is the legacy name of ``fill_id``; the deprecated ``side`` and ``action``
        # give the direction when the newer ``outcome_side`` and ``book_side`` are missing
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if not data.get("fill_id") and data.get("trade_id"):
            data["fill_id"] = data["trade_id"]
        outcome, book = data.get("outcome_side"), data.get("book_side")
        if not outcome and data.get("side") in ("yes", "no") and data.get("action") in BUY_SELL:
            buying = data["action"] == "buy"
            outcome = data["side"] if buying else ("no" if data["side"] == "yes" else "yes")
        if not outcome and book in ("bid", "ask"):
            outcome = "yes" if book == "bid" else "no"
        if not book and outcome in ("yes", "no"):
            book = "bid" if outcome == "yes" else "ask"
        data["outcome_side"], data["book_side"] = outcome, book
        return data

    @model_validator(mode="after")
    def _consistent(self) -> "Fill":
        check_prices_add_to_a_dollar(self.yes_price_e6, self.no_price_e6)
        if (self.outcome_side == "yes") != (self.book_side == "bid"):
            raise ValueError("outcome_side and book_side disagree")
        return self


class Settlement(Model):
    ticker: str
    event_ticker: str
    exchange_index: int
    market_result: str  # yes, no or scalar
    yes_count_e2: RequiredCountE2 = Field(validation_alias="yes_count_fp")
    yes_total_cost_e6: Dollars = Field(validation_alias="yes_total_cost_dollars")
    no_count_e2: RequiredCountE2 = Field(validation_alias="no_count_fp")
    no_total_cost_e6: Dollars = Field(validation_alias="no_total_cost_dollars")
    revenue_e6: CentsAsE6 = Field(validation_alias="revenue")
    fee_e6: Dollars = Field(validation_alias="fee_cost")
    settled_time: AwareDatetime
    value_e6: OptCentsAsE6 = Field(default=None, validation_alias="value")  # one YES contract


class Transfer(Model):
    """A deposit or a withdrawal (the same shape)."""

    id: str
    status: str  # pending, applied, failed or returned
    type: str  # ach, wire, crypto, debit or apm
    amount_e6: CentsAsE6 = Field(validation_alias="amount_cents")
    fee_e6: CentsAsE6 = Field(validation_alias="fee_cents")
    created_ts: int  # Unix time; the unit is not documented
    finalized_ts: int | None = None


class InstanceTransfer(Model):
    """Money moved between the account's exchange instances or shards."""

    transfer_id: str
    client_transfer_id: Text = None
    source: str  # event_contract or margined
    destination: str
    source_exchange_shard: int
    destination_exchange_shard: int
    amount_e6: Dollars = Field(validation_alias="amount")
    status: str  # pending or complete
    created_ts: int  # Unix time; the unit is not documented


class BucketLimit(Model):
    refill_rate: int  # tokens a second
    bucket_capacity: int


class UsageGrant(Model):
    exchange_instance: str
    level: str
    expires_ts: int | None = None  # Unix seconds; absent for a permanent grant
    source: Text = None


class AccountLimits(Model):
    usage_tier: str
    read: BucketLimit
    write: BucketLimit
    grants: list[UsageGrant] = []


def _transfer_limit(limit: int) -> int:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= TRANSFER_PAGE_SIZE
    ):
        raise ParameterError(f"limit must be between 1 and {TRANSFER_PAGE_SIZE}, got {limit!r}")
    return limit


class Account(Reader):
    """The owner's account, read-only."""

    def __init__(self, client: KalshiAccountClient) -> None:
        super().__init__(client)

    async def balance(self) -> Balance:
        """The available balance and portfolio value (Kalshi gives this its own small rate bucket,
        so call it rarely)."""
        return await self._object("/portfolio/balance", None, Balance)

    async def limits(self) -> AccountLimits:
        """The account's API usage tier and its read and write token buckets."""
        return await self._object("/account/limits", None, AccountLimits)

    async def positions(
        self, *, settlement_status: str = "unsettled", historical: bool = False
    ) -> Positions:
        """Every market and event position, all pages gathered. ``historical`` reads the settled
        positions already moved to Kalshi's archive instead."""
        path = "/historical/positions" if historical else "/portfolio/positions"
        params: dict[str, Any] = {"limit": MAX_PAGE_SIZE}
        if not historical:
            params["settlement_status"] = _choice(
                "settlement_status", settlement_status, SETTLEMENT_STATUSES
            )
        markets: list[MarketPosition] = []
        events: list[EventPosition] = []
        async for payload, _, _ in self._client.payloads(path, params):
            markets += _rows(MarketPosition, payload, "market_positions", f"GET {path}")
            events += _rows(EventPosition, payload, "event_positions", f"GET {path}")
        return Positions(market_positions=markets, event_positions=events)

    def orders(
        self,
        *,
        status: str = "resting",
        limit: int = MAX_PAGE_SIZE,
        cursor: str | None = None,
    ) -> AsyncIterator[TypedPage[Order]]:
        """The owner's orders with ``status`` (resting ones by default); GET only."""
        params = {"status": _choice("status", status, ORDER_STATUSES), "limit": _limit(limit)}
        return self._typed_pages("/portfolio/orders", "orders", Order, params, cursor)

    def fills(
        self,
        *,
        min_ts: int | None = None,
        max_ts: int | None = None,
        historical: bool = False,
        limit: int = MAX_PAGE_SIZE,
        cursor: str | None = None,
    ) -> AsyncIterator[TypedPage[Fill]]:
        """The account's fills, live or (``historical``) from before Kalshi's archive cutoff."""
        _range("min_ts", min_ts, "max_ts", max_ts)
        params = {"min_ts": min_ts, "max_ts": max_ts, "limit": _limit(limit)}
        path = "/historical/fills" if historical else "/portfolio/fills"
        return self._typed_pages(path, "fills", Fill, params, cursor)

    def settlements(
        self,
        *,
        min_ts: int | None = None,
        max_ts: int | None = None,
        limit: int = MAX_PAGE_SIZE,
        cursor: str | None = None,
    ) -> AsyncIterator[TypedPage[Settlement]]:
        _range("min_ts", min_ts, "max_ts", max_ts)
        params = {"min_ts": min_ts, "max_ts": max_ts, "limit": _limit(limit)}
        return self._typed_pages(
            "/portfolio/settlements", "settlements", Settlement, params, cursor
        )

    def _transfers[M: Model](
        self, path: str, key: str, model: type[M], limit: int, cursor: str | None
    ) -> AsyncIterator[TypedPage[M]]:
        params: Mapping[str, Any] = {"limit": _transfer_limit(limit)}
        return self._typed_pages(path, key, model, params, cursor)

    def deposits(
        self, *, limit: int = TRANSFER_PAGE_SIZE, cursor: str | None = None
    ) -> AsyncIterator[TypedPage[Transfer]]:
        return self._transfers("/portfolio/deposits", "deposits", Transfer, limit, cursor)

    def withdrawals(
        self, *, limit: int = TRANSFER_PAGE_SIZE, cursor: str | None = None
    ) -> AsyncIterator[TypedPage[Transfer]]:
        return self._transfers("/portfolio/withdrawals", "withdrawals", Transfer, limit, cursor)

    def instance_transfers(
        self, *, limit: int = TRANSFER_PAGE_SIZE, cursor: str | None = None
    ) -> AsyncIterator[TypedPage[InstanceTransfer]]:
        return self._transfers(
            "/portfolio/intra_exchange_instance_transfers",
            "transfers",
            InstanceTransfer,
            limit,
            cursor,
        )
