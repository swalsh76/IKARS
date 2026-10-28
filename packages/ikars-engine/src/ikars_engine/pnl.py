"""Profit and loss after fees, on entry and exit (PLAN D7; M1.12b): one place for recommendations,
their scores and the portfolio's way of counting.

A contract is bought at the ask of its side and sold at the bid of its side (a YES bid ``b`` is a NO
ask of ``1 - b``). Each leg pays Kalshi's taker fee for the series, rounded up to a cent per order
(``ikars_core.fees``; the portfolio counts exits the same way). A market whose fee cannot be
modelled gives no number rather than a guess.
"""

from ikars_core.fees import FeeSchedule, round_up
from ikars_core.fixedpoint import E6, CountE2, DollarsE6

from ikars_engine.ledger_import import value_e6
from ikars_engine.portfolio import CENT_E6


def taker_fee_e6(fees: FeeSchedule, count_e2: int, price_e6: int) -> int:
    """One order's taker fee, as Kalshi charges a member who is not direct (a cent at a time)."""
    if price_e6 <= 0:
        return 0  # nothing to sell into: no trade, no fee
    return round_up(fees.taker_fee_e6(CountE2(count_e2), DollarsE6(price_e6)), CENT_E6)


def buy_price_e6(side: str, yes_bid_e6: int | None, yes_ask_e6: int | None) -> int | None:
    """What one contract of ``side`` costs now (its ask), or None without one."""
    if side == "yes":
        return yes_ask_e6
    return None if yes_bid_e6 is None else E6 - yes_bid_e6


def sell_price_e6(side: str, yes_bid_e6: int | None, yes_ask_e6: int | None) -> int | None:
    """What one contract of ``side`` sells for now (its bid), or None without one."""
    if side == "yes":
        return yes_bid_e6
    return None if yes_ask_e6 is None else E6 - yes_ask_e6


def round_trip_e6(fees: FeeSchedule, count_e2: int, entry_e6: int, exit_e6: int) -> int:
    """Bought at ``entry_e6`` and sold at ``exit_e6``: the dollars made (millionths), after both
    legs' fees."""
    gross = value_e6(exit_e6, count_e2) - value_e6(entry_e6, count_e2)
    return gross - taker_fee_e6(fees, count_e2, entry_e6) - taker_fee_e6(fees, count_e2, exit_e6)
