"""Property tests: the same facts hold for any input, not just the chosen examples."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from ikars_core.fees import maker_fee_e6, round_up, taker_fee_e6
from ikars_core.fixedpoint import (
    E6,
    INT64_MAX,
    CountE2,
    DollarsE6,
    PrecisionError,
    from_e2,
    from_e6,
    to_e2,
    to_e6,
)

MANY = settings(max_examples=500, deadline=None)

amounts = st.integers(min_value=-INT64_MAX, max_value=INT64_MAX)
prices = st.integers(min_value=0, max_value=E6).map(DollarsE6)
counts = st.integers(min_value=0, max_value=10**12).map(CountE2)
multipliers = st.integers(min_value=0, max_value=10 * E6)


@MANY
@given(amounts)
def test_fixed_point_round_trips(value: int) -> None:
    assert to_e6(from_e6(value)) == value
    assert to_e2(from_e2(value)) == value


@MANY
@given(amounts)
def test_the_text_of_a_value_parses_back_to_it(value: int) -> None:
    assert to_e6(str(from_e6(value))) == value
    assert to_e2(str(from_e2(value))) == value


@MANY
@given(st.decimals(allow_nan=False, allow_infinity=False, places=9, min_value=-10, max_value=10))
def test_a_value_finer_than_a_millionth_is_refused_not_rounded(value: Decimal) -> None:
    if value * E6 == (value * E6).to_integral_value():
        assert to_e6(value) == int(value * E6)
    else:
        with pytest.raises(PrecisionError):
            to_e6(value)


@MANY
@given(multipliers, counts, prices)
def test_the_fee_is_symmetric_between_a_price_and_its_mirror(
    m: int, c: CountE2, p: DollarsE6
) -> None:
    assert taker_fee_e6(m, c, p) == taker_fee_e6(m, c, DollarsE6(E6 - p))


@MANY
@given(multipliers, counts, prices)
def test_the_fee_never_exceeds_its_ceiling(m: int, c: CountE2, p: DollarsE6) -> None:
    # at 50 cents P(1-P) = 1/4, the maximum: fee <= M x 0.07 x C / 4, rounded up
    ceiling = -(-(m * 7 * c) // (4 * 100 * 100))
    assert 0 <= taker_fee_e6(m, c, p) <= ceiling


@MANY
@given(multipliers, counts, prices, st.integers(min_value=1, max_value=1000))
def test_more_contracts_never_cost_less(m: int, c: CountE2, p: DollarsE6, extra: int) -> None:
    assert taker_fee_e6(m, CountE2(c + extra), p) >= taker_fee_e6(m, c, p)


@MANY
@given(multipliers, counts, prices)
def test_the_fee_is_zero_at_the_ends_of_the_price_range(m: int, c: CountE2, p: DollarsE6) -> None:
    assert taker_fee_e6(m, c, DollarsE6(0)) == 0
    assert taker_fee_e6(m, c, DollarsE6(E6)) == 0


@MANY
@given(multipliers, counts, prices, st.integers(min_value=0, max_value=100))
def test_a_maker_never_pays_more_than_a_taker(m: int, c: CountE2, p: DollarsE6, share: int) -> None:
    assert maker_fee_e6(m, share, c, p) <= taker_fee_e6(m, c, p)


@MANY
@given(multipliers, counts, prices)
def test_the_full_maker_share_is_the_taker_fee(m: int, c: CountE2, p: DollarsE6) -> None:
    assert maker_fee_e6(m, 100, c, p) == taker_fee_e6(m, c, p)
    assert maker_fee_e6(m, 0, c, p) == 0


@MANY
@given(
    st.integers(min_value=-(10**12), max_value=10**12), st.integers(min_value=1, max_value=10**7)
)
def test_round_up_is_the_least_multiple_not_below_the_value(value: int, unit: int) -> None:
    result = round_up(value, unit)
    assert result % unit == 0
    assert result >= value
    assert result - unit < value
