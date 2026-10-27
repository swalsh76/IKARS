import pytest
from ikars_core.fees import (
    MAKER_SHARE_E2,
    FeeSchedule,
    fee_schedule,
    maker_fee_e6,
    round_up,
    taker_fee_e6,
)
from ikars_core.fixedpoint import E6, INT64_MAX, CountE2, DollarsE6, to_e2, to_e6


def count(text: str) -> CountE2:
    return to_e2(text)


def price(text: str) -> DollarsE6:
    return to_e6(text)


def test_one_contract_at_three_cents_matches_the_live_run() -> None:
    # KalTerm3 live run: 1 contract at $0.03 modelled as $0.002037, charged $0.0021
    # (the account aligns to $0.0001).
    fee = taker_fee_e6(E6, count("1"), price("0.03"))
    assert fee == 2_037
    assert round_up(fee, 100) == 2_100


def test_a_sale_at_two_cents_matches_the_live_run() -> None:
    # The same run: selling 1 at $0.02 modelled as $0.001372, charged $0.0014.
    fee = taker_fee_e6(E6, count("1"), price("0.02"))
    assert fee == 1_372
    assert round_up(fee, 100) == 1_400


def test_the_fee_is_largest_at_fifty_cents() -> None:
    # 0.07 x 100 x 0.5 x 0.5 = 1.75 dollars exactly
    assert taker_fee_e6(E6, count("100"), price("0.5")) == 1_750_000


def test_prices_of_zero_and_one_cost_nothing() -> None:
    assert taker_fee_e6(E6, count("100"), price("0")) == 0
    assert taker_fee_e6(E6, count("100"), price("1")) == 0


def test_the_fee_rounds_up_never_down() -> None:
    # 0.07 x 0.01 contracts x 0.000001 is far below a millionth of a dollar, so it rounds up to 1
    assert taker_fee_e6(E6, count("0.01"), price("0.000001")) == 1
    assert taker_fee_e6(0, count("100"), price("0.5")) == 0


def test_the_multiplier_scales_the_fee() -> None:
    assert taker_fee_e6(500_000, count("100"), price("0.5")) == 875_000
    assert taker_fee_e6(2_000_000, count("100"), price("0.5")) == 3_500_000


def test_a_sale_costs_the_same_as_the_mirror_purchase() -> None:
    for p in ("0.03", "0.2", "0.77"):
        mirror = to_e6(1) - price(p)
        assert taker_fee_e6(E6, count("5"), price(p)) == taker_fee_e6(
            E6, count("5"), DollarsE6(mirror)
        )


def test_maker_fee_is_a_quarter_of_the_taker_coefficient() -> None:
    # 0.0175 x 100 x 0.25 = 0.4375 dollars exactly
    assert maker_fee_e6(E6, 25, count("100"), price("0.5")) == 437_500
    assert maker_fee_e6(E6, 50, count("100"), price("0.5")) == 875_000


def test_a_series_without_maker_fees_charges_no_maker_fee() -> None:
    assert maker_fee_e6(E6, 0, count("100"), price("0.5")) == 0


def test_maker_fee_rounds_up() -> None:
    # 0.0175 x 0.01 x 0.5 x 0.5 = 0.0000437... dollars, so 44 millionths
    assert maker_fee_e6(E6, 25, count("0.01"), price("0.5")) == 44


@pytest.mark.parametrize(
    ("args", "kind"),
    [
        ((E6, 100, -1), "price"),
        ((E6, 100, E6 + 1), "price"),
        ((E6, -1, 5), "count"),
        ((-1, 100, 5), "multiplier"),
        ((INT64_MAX + 1, 100, 5), "multiplier"),
        ((E6, INT64_MAX + 1, 5), "count"),
    ],
)
def test_out_of_domain_inputs_are_refused(args: tuple[int, int, int], kind: str) -> None:
    multiplier, cnt, prc = args
    with pytest.raises(ValueError, match=kind):
        taker_fee_e6(multiplier, CountE2(cnt), DollarsE6(prc))
    with pytest.raises(ValueError, match=kind):
        maker_fee_e6(multiplier, 25, CountE2(cnt), DollarsE6(prc))


@pytest.mark.parametrize("share", [-1, 101])
def test_a_maker_share_outside_zero_to_a_hundred_is_refused(share: int) -> None:
    with pytest.raises(ValueError, match="share"):
        maker_fee_e6(E6, share, count("1"), price("0.5"))


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (0, 1, 0),
        (5, 1, 5),
        (99, 100, 100),
        (100, 100, 100),
        (101, 100, 200),
        (-99, 100, 0),
        (-101, 100, -100),
    ],
)
def test_round_up_goes_to_the_next_multiple(value: int, unit: int, expected: int) -> None:
    assert round_up(value, unit) == expected


@pytest.mark.parametrize("unit", [0, -1, -10_000])
def test_round_up_refuses_a_unit_below_one(unit: int) -> None:
    with pytest.raises(ValueError, match="unit"):
        round_up(5, unit)


# The fee types and multipliers seen on Kalshi's public series list on 2026-10-21 (15,336 series).
@pytest.mark.parametrize(
    ("fee_type", "multiplier", "expected"),
    [
        ("quadratic", 1_000_000, FeeSchedule(1_000_000, 0)),  # 15,141 series
        ("quadratic_with_maker_fees", 1_000_000, FeeSchedule(1_000_000, 25)),  # 159
        ("quadratic", 500_000, FeeSchedule(500_000, 0)),  # 18
        ("quadratic", 0, FeeSchedule(0, 0)),  # 14
        ("quadratic_with_combo_maker_fees", 1_000_000, FeeSchedule(1_000_000, 50)),  # 3
        ("quadratic_with_maker_fees", 500_000, FeeSchedule(500_000, 25)),  # 1
    ],
)
def test_every_fee_type_in_real_use_has_a_schedule(
    fee_type: str, multiplier: int, expected: FeeSchedule
) -> None:
    assert fee_schedule(fee_type, multiplier) == expected


@pytest.mark.parametrize(
    ("fee_type", "multiplier"),
    [
        ("flat", 1_000_000),
        ("margin_market_maker_program_fees", 1_000_000),
        ("mystery", 1_000_000),
        ("Quadratic", 1_000_000),
        ("", 1_000_000),
        (None, 1_000_000),
        ("quadratic", None),
        ("quadratic", -1),
        ("quadratic", INT64_MAX + 1),
    ],
)
def test_anything_unmodelled_has_no_schedule(fee_type: str | None, multiplier: int | None) -> None:
    assert fee_schedule(fee_type, multiplier) is None


def test_the_schedule_computes_both_fees() -> None:
    schedule = fee_schedule("quadratic_with_maker_fees", E6)
    assert schedule is not None
    assert schedule.taker_fee_e6(count("100"), price("0.5")) == 1_750_000
    assert schedule.maker_fee_e6(count("100"), price("0.5")) == 437_500
    assert MAKER_SHARE_E2["quadratic_with_maker_fees"] == 25
