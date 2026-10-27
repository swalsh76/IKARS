"""ikars_core against the golden vectors generated from the KalshiTerm2 reference.

The vectors are committed (tools/vectors/ regenerates them); CI never runs the reference.
Where ikars_core is deliberately stricter the vector's ``expect`` differs from ``reference``.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from ikars_core.fees import (
    MAKER_SHARE_E2,
    fee_schedule,
    maker_fee_e6,
    round_up,
    taker_fee_e6,
)
from ikars_core.fixedpoint import CountE2, DollarsE6, PrecisionError, from_e2, from_e6, to_e2, to_e6

VECTORS = Path(__file__).parent / "vectors"


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((VECTORS / name).read_text(encoding="utf-8"))
    assert data["referenceCommit"]
    return data


def run(fn: Any, *args: Any) -> int | str:
    try:
        return int(fn(*args))
    except ValueError:  # PrecisionError is a ValueError
        return "refuse"


FIXED = load("fixedpoint.json")
FEES = load("fees.json")


@pytest.mark.parametrize("case", FIXED["cases"], ids=lambda c: f"{c['scale']}:{c['text']!r}")
def test_text_to_fixed_point(case: dict[str, Any]) -> None:
    convert = to_e6 if case["scale"] == "e6" else to_e2
    assert run(convert, case["text"]) == case["expect"]


def test_the_deliberate_differences_are_all_accounted_for() -> None:
    differing = [c for c in FIXED["cases"] if c["reference"] != c["expect"]]
    assert differing  # the vectors do pin the stricter behaviour
    assert all(c.get("reason") in {"grammar", "inexact", "bigint"} for c in differing)


@pytest.mark.parametrize("trip", FIXED["roundTrips"], ids=lambda t: str(t["value"]))
def test_round_trips_match_the_reference(trip: dict[str, Any]) -> None:
    if trip["e6"] is not None:
        assert from_e6(trip["value"]) == type(from_e6(0))(trip["e6"])
        assert to_e6(from_e6(trip["value"])) == trip["value"]
    if trip["e2"] is not None:
        assert from_e2(trip["value"]) == type(from_e2(0))(trip["e2"])


def test_taker_fee_vectors() -> None:
    for case in FEES["takerFee"]:
        got = run(
            taker_fee_e6, case["multiplier"], CountE2(case["count"]), DollarsE6(case["price"])
        )
        assert got == case["expect"], case


def test_maker_fee_vectors() -> None:
    for case in FEES["makerFee"]:
        got = run(
            maker_fee_e6,
            case["multiplier"],
            case["share"],
            CountE2(case["count"]),
            DollarsE6(case["price"]),
        )
        assert got == case["expect"], case


def test_round_up_vectors() -> None:
    for case in FEES["roundUp"]:
        assert run(round_up, case["value"], case["unit"]) == case["expect"], case


def test_schedule_vectors() -> None:
    assert FEES["makerShare"] == MAKER_SHARE_E2
    for case in FEES["schedules"]:
        schedule = fee_schedule(case["feeType"], case["multiplier"])
        if case["expect"] is None:
            assert schedule is None, case
        else:
            assert schedule is not None, case
            assert schedule.multiplier_e6 == case["expect"]["multiplier"], case
            assert schedule.maker_share_e2 == case["expect"]["makerShare"], case


def test_the_precision_error_is_a_value_error() -> None:
    assert issubclass(PrecisionError, ValueError)
