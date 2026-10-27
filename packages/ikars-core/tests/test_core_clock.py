"""Clock skew: the estimate's interval always contains the true offset."""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ikars_core.clock import (
    ClockCheckError,
    ClockSample,
    FrameDelays,
    estimate_skew,
    parse_http_date,
    sample_from_response,
)

DATE = "Sat, 24 Oct 2026 12:00:00 GMT"
DATE_EPOCH = 1_792_843_200.0


def test_an_http_date_becomes_epoch_seconds() -> None:
    assert parse_http_date(DATE) == DATE_EPOCH


@pytest.mark.parametrize("bad", ["", "yesterday", "Sat, 99 Zzz 2026 12:00:00 GMT"])
def test_an_unreadable_date_is_an_error(bad: str) -> None:
    with pytest.raises(ClockCheckError, match="unparseable Date header"):
        parse_http_date(bad)


def test_a_sample_uses_the_date_header_whatever_its_case() -> None:
    sample = sample_from_response(10.0, 11.0, {"DATE": DATE})
    assert (sample.sent, sample.received, sample.server) == (10.0, 11.0, DATE_EPOCH)


def test_a_cached_response_is_aged_by_its_age_header() -> None:
    assert sample_from_response(0, 1, {"Date": DATE, "Age": "30"}).server == DATE_EPOCH + 30
    assert sample_from_response(0, 1, {"Date": DATE, "Age": "soon"}).server == DATE_EPOCH


def test_a_response_with_no_date_cannot_be_used() -> None:
    with pytest.raises(ClockCheckError, match="no Date header"):
        sample_from_response(0, 1, {"Content-Type": "text/plain"})


def test_one_sample_gives_an_interval_not_a_point() -> None:
    skew = estimate_skew([ClockSample(sent=100.0, received=100.5, server=99.0)])
    assert (skew.low, skew.high) == (0.0, 1.5)
    assert skew.offset == 0.75 and skew.uncertainty == 0.75 and skew.consistent
    assert skew.status == "ok" and skew.samples == 1


def test_more_samples_narrow_the_interval() -> None:
    one = estimate_skew([ClockSample(100.0, 100.5, 99.0)])
    two = estimate_skew([ClockSample(100.0, 100.5, 99.0), ClockSample(200.3, 200.8, 200.0)])
    assert two.uncertainty < one.uncertainty
    assert two.low == 0.0 and two.high == pytest.approx(0.8)


def test_samples_that_disagree_fall_back_to_the_median_and_say_so() -> None:
    skew = estimate_skew(
        [
            ClockSample(100.0, 100.1, 99.0),
            ClockSample(200.0, 200.1, 190.0),
            ClockSample(300, 300.1, 299),
        ]
    )
    assert not skew.consistent and "low confidence" in skew.describe()


def test_a_clock_far_out_is_skewed_and_one_near_the_limit_is_uncertain() -> None:
    ahead = estimate_skew([ClockSample(110.0, 110.1, 100.0)])  # 9 to 10 s ahead
    assert ahead.status == "skewed" and ahead.offset_ms == pytest.approx(9500, abs=100)
    behind = estimate_skew([ClockSample(100.0, 100.1, 110.0)])
    assert behind.status == "skewed" and behind.offset < 0
    edge = estimate_skew([ClockSample(102.0, 102.1, 100.0)])  # between 1 and 2.1 s ahead
    assert edge.status == "uncertain"


def test_the_threshold_can_be_changed() -> None:
    sample = [ClockSample(103.0, 103.1, 100.0)]  # 2 to 3.1 s ahead
    assert estimate_skew(sample, threshold=2.0).status in ("skewed", "uncertain")
    assert estimate_skew(sample, threshold=10.0).status == "ok"


def test_no_samples_is_an_error() -> None:
    with pytest.raises(ClockCheckError, match="no samples"):
        estimate_skew([])


def test_the_description_says_which_way_and_how_sure() -> None:
    text = estimate_skew([ClockSample(105.0, 105.2, 100.0)]).describe()
    assert "ahead of Kalshi" in text and "1 samples" in text
    assert "behind Kalshi" in estimate_skew([ClockSample(100.0, 100.2, 105.0)]).describe()


@given(
    true_offset=st.floats(-30, 30),
    starts=st.lists(st.floats(1e9, 2e9), min_size=1, max_size=6),
    trips=st.lists(st.floats(0.001, 2.0), min_size=6, max_size=6),
    where=st.lists(st.floats(0, 1), min_size=6, max_size=6),
)
def test_the_interval_always_contains_the_true_offset(
    true_offset: float, starts: list[float], trips: list[float], where: list[float]
) -> None:
    samples = []
    for sent, trip, fraction in zip(starts, trips, where, strict=False):
        stamped_locally = sent + trip * fraction  # when the server read its clock
        server_time = stamped_locally - true_offset  # what that clock said
        samples.append(
            ClockSample(sent, sent + trip, math.floor(server_time))
        )  # Date drops fractions
    skew = estimate_skew(samples)
    assert skew.consistent
    assert skew.low - 1e-6 <= true_offset <= skew.high + 1e-6


def test_the_quickest_frame_bounds_the_offset_from_above() -> None:
    delays = FrameDelays()
    assert delays.upper_bound_seconds() is None and delays.count == 0
    for received, sending in (
        (1_000_250, 1_000_000),
        (2_000_040, 2_000_000),
        (3_000_900, 3_000_000),
    ):
        delays.add(received, sending)
    assert delays.upper_bound_seconds() == 0.04 and delays.count == 3


def test_old_frames_fall_out_of_the_window() -> None:
    delays = FrameDelays(window=2)
    delays.add(1_000_010, 1_000_000)  # 10 ms: the best, but it will be forgotten
    delays.add(2_000_500, 2_000_000)
    delays.add(3_000_300, 3_000_000)
    assert delays.upper_bound_seconds() == 0.3
