from decimal import Decimal

import pytest
from ikars_core.fixedpoint import (
    INT64_MAX,
    PrecisionError,
    from_e2,
    from_e6,
    to_e2,
    to_e6,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("0.4100"), 410_000),
        ("0.0001", 100),  # the finest tick seen in live data
        ("0.000001", 1),  # the finest Kalshi documents
        (1, 1_000_000),
        ("118.7499", 118_749_900),  # a strike level
        ("-3.5", -3_500_000),
        ("+0.5", 500_000),
        (".5", 500_000),
        ("1.", 1_000_000),
        ("000123.4500", 123_450_000),
        ("-0", 0),
        ("0", 0),
        (0, 0),
        (Decimal("1E+2"), 100_000_000),  # an exponent form is fine as a Decimal, not as text
        (Decimal("-0"), 0),
    ],
)
def test_to_e6_is_exact(value: str | int | Decimal, expected: int) -> None:
    assert to_e6(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("104.24", 10_424), ("0.03", 3), (7, 700), ("0.01", 1), (Decimal("2.50"), 250)],
)
def test_to_e2_is_exact(value: str | int | Decimal, expected: int) -> None:
    assert to_e2(value) == expected


def test_none_stays_none() -> None:
    assert to_e6(None) is None
    assert to_e2(None) is None


@pytest.mark.parametrize("value", ["0.1234567", "0.0000001", "1.0000001", "-0.0000001"])
def test_more_precision_than_the_scale_is_refused(value: str) -> None:
    with pytest.raises(PrecisionError):
        to_e6(value)


@pytest.mark.parametrize("value", ["0.001", "0.123", "1.005"])
def test_counts_finer_than_a_hundredth_are_refused(value: str) -> None:
    with pytest.raises(PrecisionError):
        to_e2(value)


def test_a_long_inexact_value_is_not_rounded_into_looking_exact() -> None:
    # A 28-digit decimal context would round this to exactly 1 dollar.
    with pytest.raises(PrecisionError):
        to_e6("1.0000000000000000000000000001")
    with pytest.raises(PrecisionError):
        to_e6(Decimal("1.0000000000000000000000000001"))


def test_a_long_but_exact_value_is_accepted() -> None:
    assert to_e6("1.000000000000000000000000000") == 1_000_000
    assert to_e6("0.410000000000000000000000000000") == 410_000
    assert to_e6("00000000000000000000000000000000000012.5") == 12_500_000


@pytest.mark.parametrize(
    "value",
    [
        "1e6", "1E-6", "2.5e-1", " 1", "1 ", "\t1", "1_000", "1,000", "0x10", "--1", "+-1",
        "1.2.3", ".", "+", "-", "", "NaN", "Infinity", "-Infinity", "nan", "inf",
        "١٢", "１", "1 ", "1/2", "0.5%", "$1",
    ],
)  # fmt: skip
def test_text_that_is_not_plain_ascii_decimal_is_refused(value: str) -> None:
    with pytest.raises(PrecisionError):
        to_e6(value)
    with pytest.raises(PrecisionError):
        to_e2(value)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimals_are_refused(value: Decimal) -> None:
    with pytest.raises(PrecisionError):
        to_e6(value)


def test_an_absurd_exponent_is_refused_without_computing_it() -> None:
    with pytest.raises(PrecisionError):
        to_e6(Decimal("1E+999999999"))
    with pytest.raises(PrecisionError):
        to_e6(Decimal("1E-999999999"))
    assert to_e6(Decimal("0E+999999999")) == 0


@pytest.mark.parametrize("value", [0.41, 1.0, float("nan"), float("inf"), True, False])
def test_floats_and_bools_are_refused(value: object) -> None:
    with pytest.raises(TypeError):
        to_e6(value)  # type: ignore[call-overload]
    with pytest.raises(TypeError):
        to_e2(value)  # type: ignore[call-overload]


@pytest.mark.parametrize("value", [b"1", [1], (1,), object()])
def test_other_types_are_refused(value: object) -> None:
    with pytest.raises(TypeError):
        to_e6(value)  # type: ignore[call-overload]


def test_the_bigint_edge_at_each_scale() -> None:
    assert to_e6("9223372036854.775807") == INT64_MAX
    assert to_e6("-9223372036854.775807") == -INT64_MAX
    with pytest.raises(PrecisionError):
        to_e6("9223372036854.775808")
    assert to_e2("92233720368547758.07") == INT64_MAX
    with pytest.raises(PrecisionError):
        to_e2("92233720368547758.08")
    with pytest.raises(PrecisionError):
        to_e6(INT64_MAX)  # as dollars it would overflow once scaled
    with pytest.raises(PrecisionError):
        to_e6("99999999999999999999")


@pytest.mark.parametrize("value", [0, 1, -1, 99, 100, 410_000, -3_500_000, INT64_MAX, -INT64_MAX])
def test_round_trip_through_decimal(value: int) -> None:
    assert to_e6(from_e6(value)) == value
    assert to_e2(from_e2(value)) == value


def test_from_e6_and_from_e2_are_exact_decimals() -> None:
    assert from_e6(410_000) == Decimal("0.41")
    assert from_e6(-1) == Decimal("-0.000001")
    assert from_e6(0) == 0
    assert from_e2(10_424) == Decimal("104.24")
    assert str(from_e6(INT64_MAX)) == "9223372036854.775807"  # no context rounding, all 19 digits
    assert str(from_e6(123456789012345678901234567890)) == "123456789012345678901234.567890"
