"""Kalshi trading fees as exact integer arithmetic.

Sources: Kalshi's API documentation ("Fee Rounding", series ``fee_type`` and ``fee_multiplier``),
the fee schedule, and KalshiTerm2's reading of them (``kalshi_core.fees``, 2026-10-12). The model
fee of a **taker** trade is::

    M x 0.07 x C x P x (1 - P)        M = the series multiplier, C = contracts, P = price in dollars

and the trade fee is that rounded UP to $0.000001. Kalshi then aligns the balance to $0.0001
(direct members) or $0.01 (non-direct members) and rebates the overpayment across the fills of one
order; :func:`round_up` models that conservatively as a ceiling per order. Selling YES at a price
``b`` is buying NO at ``1 - b``, and ``P(1 - P)`` is the same either way.

A **maker** (a resting order that is later filled) pays a fixed share of the taker coefficient on
series whose fee type says so: a quarter (0.0175 against 0.07) for ``quadratic_with_maker_fees``,
a half for the combo variant, nothing for plain ``quadratic``. *The maker share is taken from the
fee schedule as summarised by Kalshi's documentation and KalshiTerm2. Kalshi's API reference for
a series (checked 2026-10-23) states it: a 0.25 maker multiplier for ``quadratic_with_maker_fees``
and 0.5 "instead of 0.25" for the combo type; third-party summaries give the same 0.0175
coefficient. It has not been confirmed against a real maker fill.*

Only the ``quadratic`` family is modelled. ``flat`` and any other type give no schedule, so
callers skip rather than guess. This module computes the arithmetic for one fee schedule; which
schedule was in force at a past time comes from the series fee-change history, not from here.
"""

from dataclasses import dataclass

from ikars_core.fixedpoint import E6, INT64_MAX, CountE2, DollarsE6

# fee_type -> the maker coefficient as a share of the taker coefficient, in hundredths
MAKER_SHARE_E2 = {
    "quadratic": 0,
    "quadratic_with_maker_fees": 25,
    "quadratic_with_combo_maker_fees": 50,
}
QUADRATIC_FAMILY = frozenset(MAKER_SHARE_E2)


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """One series' fee terms: the multiplier M in millionths and the maker share in hundredths."""

    multiplier_e6: int
    maker_share_e2: int

    def taker_fee_e6(self, count_e2: CountE2, price_e6: DollarsE6) -> DollarsE6:
        return taker_fee_e6(self.multiplier_e6, count_e2, price_e6)

    def maker_fee_e6(self, count_e2: CountE2, price_e6: DollarsE6) -> DollarsE6:
        return maker_fee_e6(self.multiplier_e6, self.maker_share_e2, count_e2, price_e6)


def fee_schedule(fee_type: str | None, fee_multiplier_e6: int | None) -> FeeSchedule | None:
    """The schedule for a series' ``fee_type`` and multiplier (in millionths), or None when the fee
    cannot be modelled (an unknown or flat type, a missing or negative multiplier)."""
    if fee_type not in MAKER_SHARE_E2 or fee_multiplier_e6 is None:
        return None
    if not 0 <= fee_multiplier_e6 <= INT64_MAX:
        return None
    return FeeSchedule(fee_multiplier_e6, MAKER_SHARE_E2[fee_type])


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _check(multiplier_e6: int, count_e2: int, price_e6: int) -> None:
    if not 0 <= price_e6 <= E6:
        raise ValueError("price must be within [0, 1] dollars")
    if not 0 <= count_e2 <= INT64_MAX or not 0 <= multiplier_e6 <= INT64_MAX:
        raise ValueError("count and multiplier must be non-negative and fit a bigint")


def taker_fee_e6(multiplier_e6: int, count_e2: CountE2, price_e6: DollarsE6) -> DollarsE6:
    """The taker trade fee in millionths of a dollar for ``count_e2`` hundredths of a contract at
    ``price_e6``, rounded up to a millionth.

    fee_e6 = M x 7/100 x (count_e2/100) x (p/1e6) x (1 - p/1e6) x 1e6, kept exact as one fraction
    and rounded up with integer arithmetic.
    """
    _check(multiplier_e6, count_e2, price_e6)
    numerator = multiplier_e6 * 7 * count_e2 * price_e6 * (E6 - price_e6)
    return DollarsE6(_ceil_div(numerator, E6 * 100 * 100 * E6))


def maker_fee_e6(
    multiplier_e6: int, maker_share_e2: int, count_e2: CountE2, price_e6: DollarsE6
) -> DollarsE6:
    """The maker trade fee in millionths: the taker fee's coefficient times ``maker_share_e2``
    hundredths, in the same exact arithmetic and rounded up to a millionth. A share of zero is a
    series with no maker fee."""
    _check(multiplier_e6, count_e2, price_e6)
    if not 0 <= maker_share_e2 <= 100:
        raise ValueError("maker share must be within [0, 100] hundredths")
    numerator = multiplier_e6 * 7 * maker_share_e2 * count_e2 * price_e6 * (E6 - price_e6)
    return DollarsE6(_ceil_div(numerator, E6 * 100 * 100 * 100 * E6))


def round_up(value_e6: int, unit_e6: int) -> int:
    """Round up to a multiple of ``unit_e6`` (a unit of 1 leaves the value unchanged)."""
    if unit_e6 < 1:
        raise ValueError("rounding unit must be at least 1")
    return _ceil_div(value_e6, unit_e6) * unit_e6
