import asyncio

import pytest
from ikars_core.ratelimit import TokenBucket


class FakeTime:
    """A clock that only moves when something sleeps, so the tests never really wait."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)


def bucket(rate: float, capacity: float, fake: FakeTime) -> TokenBucket:
    return TokenBucket(rate, capacity, clock=fake.clock, sleep=fake.sleep)


async def test_a_new_bucket_is_full() -> None:
    fake = FakeTime()
    b = bucket(10, 50, fake)
    for _ in range(5):
        await b.acquire(10)
    assert fake.sleeps == []
    assert b.stats.acquired == 5 and b.stats.spent == 50
    assert b.stats.waits == 0


async def test_an_empty_bucket_waits_for_the_refill() -> None:
    fake = FakeTime()
    b = bucket(10, 10, fake)
    await b.acquire(10)
    await b.acquire(10)  # needs a second of refill at 10 tokens/s
    assert fake.sleeps == [pytest.approx(1.0)]
    assert b.stats.waits == 1
    assert b.stats.waited_seconds == pytest.approx(1.0)


async def test_idle_time_refills_but_never_beyond_capacity() -> None:
    fake = FakeTime()
    b = bucket(10, 20, fake)
    await b.acquire(20)
    fake.now += 3600  # an hour idle still only restores the capacity
    await b.acquire(20)
    assert fake.sleeps == []
    await b.acquire(10)
    assert fake.sleeps == [pytest.approx(1.0)]


async def test_a_sustained_load_is_paced_at_the_rate() -> None:
    fake = FakeTime()
    b = bucket(50, 50, fake)  # five 10-token requests per second
    start = fake.now
    for _ in range(26):
        await b.acquire(10)
    # 5 free from the full bucket, then 21 more at 5 per second
    assert fake.now - start == pytest.approx(21 / 5)


async def test_waiters_are_served_first_come_first_served() -> None:
    fake = FakeTime()
    b = bucket(10, 10, fake)
    order: list[int] = []

    async def take(n: int) -> None:
        await b.acquire(10)
        order.append(n)

    await asyncio.gather(take(1), take(2), take(3))
    assert order == [1, 2, 3]
    assert fake.now - 1000.0 == pytest.approx(2.0)


@pytest.mark.parametrize("cost", [0, -1, 11])
async def test_a_cost_outside_the_capacity_is_refused(cost: float) -> None:
    b = bucket(10, 10, FakeTime())
    with pytest.raises(ValueError, match="cost"):
        await b.acquire(cost)


@pytest.mark.parametrize(("rate", "capacity"), [(0, 1), (1, 0), (-1, 1), (1, -1)])
def test_a_bucket_needs_a_positive_rate_and_capacity(rate: float, capacity: float) -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(rate, capacity)


async def test_the_real_clock_and_sleep_are_the_defaults() -> None:
    b = TokenBucket(1000, 10)
    await b.acquire(10)
    await b.acquire(10)  # a real wait of about 10 ms
    assert b.stats.waits >= 1


async def test_the_rate_can_be_changed_and_earned_tokens_are_kept() -> None:
    fake = FakeTime()
    b = bucket(10, 100, fake)
    await b.acquire(100)  # empty
    fake.now += 5  # earns 50 tokens at the old rate
    b.set_rate(20)  # the new rate applies from now on
    assert b.rate == 20
    await b.acquire(50)  # the earned tokens are still there
    assert fake.sleeps == []
    await b.acquire(40)  # now waits at the new rate: 40 / 20
    assert fake.sleeps == [pytest.approx(2.0)]


async def test_a_smaller_capacity_trims_the_tokens_held() -> None:
    fake = FakeTime()
    b = bucket(10, 100, fake)
    b.set_rate(10, capacity=20)
    await b.acquire(20)
    await b.acquire(20)
    assert fake.sleeps == [pytest.approx(2.0)]
    with pytest.raises(ValueError, match="cost"):
        await b.acquire(30)  # beyond the new capacity


@pytest.mark.parametrize(("rate", "capacity"), [(0, None), (-1, None), (5, 0), (5, -1)])
def test_set_rate_needs_positive_values(rate: float, capacity: float | None) -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(10, 10).set_rate(rate, capacity)
