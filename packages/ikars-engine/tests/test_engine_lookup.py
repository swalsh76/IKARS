"""M1.14: settled markets after a restart. A fake Kalshi on the core package's made-up fixtures (the
test-data policy): the full discovery reads the recently closed and settled, a market the index
lacks is looked up for a screen, and the journal's unsettled markets are looked up until they
settle."""

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ikars_core.models import Market
from ikars_engine.kalshi.lookup import RETRY_NS, MarketLookup
from kalshi_fakes import RIGS, T0, Rig, market


@pytest.fixture(autouse=True)
def close_rigs() -> Iterator[None]:
    yield
    while RIGS:
        RIGS.pop().state.close()


def settled(
    ticker: str, result: str = "yes", close: str = "2026-01-01T17:00:00Z"
) -> dict[str, Any]:
    return {**market(ticker, status="settled"), "result": result, "close_time": close}


LONG_AGO = "2025-06-01T17:00:00Z"  # settled long before the index's three days


async def test_the_full_discovery_reads_the_closed_and_then_the_settled(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("O"), market("C", status="closed"), settled("S")])
    await rig.discovery.full()
    index = rig.discovery.index
    assert {"O", "C"} <= set(index.markets) and "S" not in index.markets  # not before the feed
    assert await rig.discovery.recent_settled() == 1  # after it, in the background
    assert index.markets["S"].result == "yes"
    asked = [r.url.params for r in rig.kalshi.requests if r.url.path.endswith("/markets")]
    since = T0 // 10**9 - 3 * 86_400
    assert dict(asked[2]).get("status") == "closed" and "min_close_ts" not in asked[2]  # all closed
    assert {"status": "settled", "min_settled_ts": str(since)}.items() <= dict(asked[3]).items()


async def test_the_source_reads_the_settled_after_the_feed_is_live(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [market("O"), settled("S")])
    await rig.source.start()
    for _ in range(50):
        await asyncio.sleep(0)
        if "S" in rig.discovery.index.markets:
            break
    assert "S" in rig.discovery.index.markets
    await rig.source.stop()


async def test_a_market_the_index_lacks_is_looked_up_once_and_kept(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [settled("RECENT"), settled("OLD", close=LONG_AGO)])
    rig.kalshi.historical = [settled("PAST", "no", close=LONG_AGO)]
    index = rig.discovery.index
    lookup = MarketLookup(rig.kalshi.data(), index, rig.clock.now_ns)
    recent = await lookup.get("RECENT")
    assert recent is not None and "RECENT" in index.markets  # recent: into the index
    old = await lookup.get("OLD")
    assert old is not None and old.result == "yes" and "OLD" not in index.markets  # cached only
    past = await lookup.get("PAST")  # not in the live store: the historical one
    assert past is not None and past.result == "no"
    assert await lookup.get("NOWHERE") is None
    asked = len(rig.kalshi.requests)
    assert (await lookup.get("OLD")) == old and await lookup.get("NOWHERE") is None
    assert len(rig.kalshi.requests) == asked  # a settled one and a missing one are not asked again
    assert lookup.title("PAST") == past.title and lookup.fees("PAST") is index.fees.get("KXDEMO")
    assert lookup.health()["cached"] == 4


async def test_the_journals_unsettled_markets_are_looked_up_until_they_settle(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [market("KNOWN"), settled("LIVE", close=LONG_AGO)])
    rig.kalshi.historical = [settled("GONE", "no", close=LONG_AGO)]
    index = rig.discovery.index
    index.put(Market.model_validate(market("KNOWN")))
    lookup = MarketLookup(rig.kalshi.data(), index, rig.clock.now_ns)
    assert await lookup.settle(["KNOWN", "LIVE", "GONE", "NOWHERE"]) == 3  # KNOWN: the index's
    listing = [r for r in rig.kalshi.requests if "tickers" in r.url.params]
    assert len(listing) == 1 and listing[0].url.params["tickers"] == "LIVE,GONE,NOWHERE"
    assert lookup.info("LIVE").result == "yes" and lookup.info("GONE").result == "no"  # type: ignore[union-attr]
    assert lookup.settled == 2
    assert await lookup.settle(["LIVE", "GONE", "NOWHERE"]) == 0  # known, or asked too lately
    rig.clock.advance_to(rig.clock.now_ns() + RETRY_NS)
    assert await lookup.settle(["NOWHERE"]) == 1  # asked again after the retry time


async def test_a_scalar_settlement_is_found_with_what_yes_paid(tmp_path: Path) -> None:
    walkover = {**settled("WO", "scalar", close=LONG_AGO), "settlement_value_dollars": "0.5000"}
    blank = {**settled("BLANK", "scalar", close=LONG_AGO)}  # no value: never guessed
    rig = Rig(tmp_path, [walkover, blank])
    lookup = MarketLookup(rig.kalshi.data(), rig.discovery.index, rig.clock.now_ns)
    assert await lookup.settle(["WO", "BLANK"]) == 2
    assert lookup.info("WO").settlement() == ("scalar", 500_000)  # type: ignore[union-attr]
    assert lookup.info("BLANK").settlement() is None  # type: ignore[union-attr]
    assert lookup.settled == 1


@pytest.mark.parametrize(
    ("result", "value", "expected"),
    [
        ("yes", None, ("yes", 1_000_000)),
        ("no", "0.0000", ("no", 0)),
        ("scalar", "0.5400", ("scalar", 540_000)),
        ("scalar", None, None),
        (None, None, None),  # not settled yet
    ],
)
def test_a_settlement_is_its_result_and_what_yes_paid(
    result: str | None, value: str | None, expected: tuple[str, int] | None
) -> None:
    from ikars_engine.kalshi.discovery import MarketIndex

    index = MarketIndex(now_ns=lambda: 0)
    index.put(Market.model_validate(
        {**market("M", status="finalized"), "result": result, "settlement_value_dollars": value}
    ))  # fmt: skip
    assert index.markets["M"].settlement() == expected


async def test_a_lists_missing_titles_come_in_the_background(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [settled("T1", close=LONG_AGO), settled("T2", close=LONG_AGO)])
    lookup = MarketLookup(rig.kalshi.data(), rig.discovery.index, rig.clock.now_ns)
    assert lookup.title("T1") == ""
    lookup.want(["T1", "T2", "T1"])
    for _ in range(50):
        await asyncio.sleep(0)
        if lookup.title("T1") and lookup.title("T2"):
            break
    assert lookup.title("T1") and lookup.title("T2") and lookup.version == 2
    await lookup.stop()


async def test_kalshi_failing_is_not_read_as_not_found(tmp_path: Path) -> None:
    from ikars_core.rest import KalshiAPIError

    rig = Rig(tmp_path, [market("A")])
    index = rig.discovery.index
    lookup = MarketLookup(rig.kalshi.data(), index, rig.clock.now_ns)
    index.put(Market.model_validate(market("A")))
    asked = len(rig.kalshi.requests)
    assert await lookup.get("A") is index.markets["A"] and len(rig.kalshi.requests) == asked
    rig.kalshi.fail = True  # every request answers 404 here: the fake's way to fail
    assert await lookup.get("X") is None  # 404 from both stores: not found
    rig.kalshi.fail = False

    async def broken(ticker: str) -> Market:
        raise KalshiAPIError(503, "busy")

    lookup._data.market = broken  # type: ignore[method-assign]
    with pytest.raises(KalshiAPIError):
        await lookup.get("Y")  # a busy Kalshi is an error, never "no such market"
    lookup.want(["Y"])
    for _ in range(20):
        await asyncio.sleep(0)
    assert lookup.failures == 1  # the background one is counted, not raised
    await lookup.stop()


async def test_a_settled_market_already_known_is_not_asked_again(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [settled("S", close=LONG_AGO)])
    lookup = MarketLookup(rig.kalshi.data(), rig.discovery.index, rig.clock.now_ns)
    assert await lookup.get("S") is not None
    asked = len(rig.kalshi.requests)
    rig.clock.advance_to(rig.clock.now_ns() + 2 * RETRY_NS)
    assert await lookup.settle(["S"]) == 0 and len(rig.kalshi.requests) == asked


async def test_concurrent_lookups_share_one_request_and_odd_tickers_are_never_asked(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path, [settled("S", close=LONG_AGO)])
    lookup = MarketLookup(rig.kalshi.data(), rig.discovery.index, rig.clock.now_ns)
    first, second = await asyncio.gather(lookup.get("S"), lookup.get("S"))
    assert first == second and first is not None
    assert sum(1 for r in rig.kalshi.requests if r.url.path.endswith("/markets/S")) == 1
    asked = len(rig.kalshi.requests)
    assert await lookup.get("S?status=open") is None and await lookup.get("../x") is None
    assert len(rig.kalshi.requests) == asked  # never put into a URL
    version = lookup.version
    lookup._keep("S", None)
    lookup._keep("S", None)
    assert lookup.version == version + 1  # a change once, then nothing new


async def test_a_failed_batch_never_holds_back_the_next(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [settled(f"M{n}", close=LONG_AGO) for n in range(60)])
    lookup = MarketLookup(rig.kalshi.data(), rig.discovery.index, rig.clock.now_ns)
    calls = [0]
    real = lookup._batch

    async def flaky(tickers: list[str]) -> None:
        calls[0] += 1
        if calls[0] == 1:
            raise RuntimeError("a 5xx")
        await real(tickers)

    lookup._batch = flaky  # type: ignore[method-assign]
    assert await lookup.settle([f"M{n}" for n in range(60)]) == 60
    assert lookup.failures == 1 and lookup.info("M59") is not None  # the second batch went on


async def test_the_settled_load_moves_the_index_version_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path, [settled("S1"), settled("S2"), settled("S3")])
    index = rig.discovery.index
    before = index.version
    assert await rig.discovery.recent_settled() == 3
    assert index.version == before + 1


async def test_the_background_cap_holds_within_one_call(tmp_path: Path) -> None:
    """M1.R3 B4: the cap counted lookups once they ran, so one call started every one it was given
    (up to 200 after a restart), queueing everything else that shares the request budget."""
    from ikars_engine.kalshi.lookup import BACKGROUND_AT_ONCE

    rig = Rig(tmp_path, [])
    lookup = MarketLookup(rig.kalshi.data(), rig.discovery.index, rig.clock.now_ns)
    lookup.want([f"KXT-26JAN01-{n}" for n in range(3 * BACKGROUND_AT_ONCE)])
    assert len(lookup._tasks) == BACKGROUND_AT_ONCE
    await lookup.stop()
