import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from ikars_core.config import Environment, KalshiConfig
from ikars_core.endpoints import MarketData, ParameterError
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import KalshiPublicClient, KalshiResponseError

Sample = Callable[[str], dict[str, Any]]
Handler = Callable[[httpx.Request], httpx.Response]


class Rig:
    def __init__(self, handler: Handler) -> None:
        self.requests: list[httpx.Request] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if len(self.requests) > 100:
                raise AssertionError("more than 100 requests: the client did not stop")
            return handler(request)

        async def sleep(seconds: float) -> None:
            return None

        client = KalshiPublicClient(
            KalshiConfig(environment=Environment.PRODUCTION),
            bucket=TokenBucket(1e9, 1e9),
            transport=httpx.MockTransport(record),
            sleep=sleep,
            jitter=lambda: 1.0,
        )
        self.data = MarketData(client)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def ok(body: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(body).encode())


# --- single reads -------------------------------------------------------------------------------


async def test_exchange_status_and_schedule_and_cutoff(sample: Sample) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/trade-api/v2")
        return ok(
            {
                "/exchange/status": sample("exchange_status.json"),
                "/exchange/schedule": {"schedule": sample("exchange_schedule.json")},
                "/historical/cutoff": sample("cutoff.json"),
            }[path]
        )

    rig = Rig(handler)
    assert (await rig.data.exchange_status()).trading_active
    assert (await rig.data.exchange_schedule()).standard_hours[0].thursday
    assert (await rig.data.historical_cutoff()).market_settled_ts.year == 2026


async def test_single_markets_events_and_series(sample: Sample) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/trade-api/v2")
        return ok(
            {
                "/markets/KXDEMO-26JAN01-T50": {"market": sample("market_settled.json")},
                "/historical/markets/KXDEMO-26JAN01-T50": {"market": sample("market_settled.json")},
                "/events/KXDEMO-26JAN01": {"event": sample("event.json"), "markets": []},
                "/series/KXDEMO": {"series": sample("series.json")},
            }[path]
        )

    rig = Rig(handler)
    assert (await rig.data.market("KXDEMO-26JAN01-T50")).result == "yes"
    assert (await rig.data.historical_market("KXDEMO-26JAN01-T50")).ticker == "KXDEMO-26JAN01-T50"
    assert (await rig.data.event("KXDEMO-26JAN01")).category == "Crypto"
    assert (await rig.data.series("KXDEMO")).fee_multiplier_e6 == 500_000


@pytest.mark.parametrize("body", [{}, {"market": []}, {"market": "x"}, {"market": None}])
async def test_a_single_read_without_its_object_is_an_error(body: dict[str, Any]) -> None:
    rig = Rig(lambda r: ok(body))
    with pytest.raises(KalshiResponseError, match="expected an object under 'market'"):
        await rig.data.market("KXDEMO-26JAN01-T50")


async def test_an_object_that_cannot_be_read_names_what_failed(sample: Sample) -> None:
    bad = sample("market_settled.json") | {"last_price_dollars": "0.6200001"}
    rig = Rig(lambda r: ok({"market": bad}))
    with pytest.raises(KalshiResponseError) as caught:
        await rig.data.market("KXDEMO-26JAN01-T50")
    message = str(caught.value)
    assert "Market" in message
    assert "KXDEMO-26JAN01-T50" in message
    assert "last_price_dollars" in message
    assert "more precision" in message  # why it was refused


async def test_the_status_response_is_the_object_itself(sample: Sample) -> None:
    rig = Rig(lambda r: ok({"unexpected": 1}))
    with pytest.raises(KalshiResponseError, match="cannot read ExchangeStatus"):
        await rig.data.exchange_status()


async def test_the_series_list_and_fee_changes(sample: Sample) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/series/fee_changes"):
            return ok({"series_fee_change_arr": [sample("fee_change.json")]})
        return ok(
            {"series": [sample("series.json"), sample("series.json") | {"ticker": "KXOTHER"}]}
        )

    rig = Rig(handler)
    assert [s.ticker for s in await rig.data.series_list()] == ["KXDEMO", "KXOTHER"]
    assert (await rig.data.series_fee_changes())[0].series_ticker == "KXDEMO"
    assert rig.last.url.params["show_historical"] == "true"
    assert "series_ticker" not in rig.last.url.params
    await rig.data.series_fee_changes("KXDEMO", show_historical=False)
    assert rig.last.url.params["series_ticker"] == "KXDEMO"
    assert rig.last.url.params["show_historical"] == "false"


@pytest.mark.parametrize("body", [{}, {"series": "x"}, {"series": [1]}])
async def test_a_series_list_that_is_not_a_list_of_objects_is_an_error(
    body: dict[str, Any],
) -> None:
    rig = Rig(lambda r: ok(body))
    with pytest.raises(KalshiResponseError):
        await rig.data.series_list()
    with pytest.raises(KalshiResponseError):
        await rig.data.series_fee_changes()


# --- listings -----------------------------------------------------------------------------------


def paged(sample: Sample, pages: list[list[str]]) -> Handler:
    """Pages of markets: each inner list holds the tickers of a page; the cursor is its number."""

    def handler(request: httpx.Request) -> httpx.Response:
        index = int(request.url.params.get("cursor") or 0)
        rows = [sample("market_settled.json") | {"ticker": t} for t in pages[index]]
        return ok({"markets": rows, "cursor": str(index + 1) if index + 1 < len(pages) else ""})

    return handler


async def test_historical_markets_page_with_typed_items_and_raw_json(sample: Sample) -> None:
    rig = Rig(paged(sample, [["A", "B"], ["C"]]))
    pages = [
        p async for p in rig.data.historical_markets(series_ticker="KXDEMO", mve_filter="exclude")
    ]
    assert [[m.ticker for m in p.items] for p in pages] == [["A", "B"], ["C"]]
    assert [[r["ticker"] for r in p.raw] for p in pages] == [["A", "B"], ["C"]]
    assert "some_future_field" in pages[0].raw[0]  # the original JSON keeps what the model drops
    assert [(p.request_cursor, p.next_cursor) for p in pages] == [(None, "1"), ("1", None)]
    first = rig.requests[0].url
    assert first.path.endswith("/historical/markets")
    assert (first.params["limit"], first.params["series_ticker"], first.params["mve_filter"]) == (
        "1000",
        "KXDEMO",
        "exclude",
    )


async def test_a_listing_can_resume_from_a_saved_cursor(sample: Sample) -> None:
    rig = Rig(paged(sample, [["A"], ["B"], ["C"]]))
    pages = [p async for p in rig.data.markets(status="settled", cursor="1")]
    assert [m.ticker for p in pages for m in p.items] == ["B", "C"]
    assert rig.requests[0].url.params["cursor"] == "1"


async def test_a_bad_row_in_a_listing_is_an_error_that_names_it(sample: Sample) -> None:
    rows = [
        sample("market_settled.json") | {"ticker": "GOOD"},
        sample("market_settled.json") | {"ticker": "BAD", "volume_fp": "1.001"},
    ]
    rig = Rig(lambda r: ok({"markets": rows, "cursor": ""}))
    with pytest.raises(KalshiResponseError, match="BAD"):
        _ = [p async for p in rig.data.markets()]


async def test_a_tolerant_listing_sets_the_bad_row_aside_and_returns_the_rest(
    sample: Sample,
) -> None:
    rows = [
        sample("market_settled.json") | {"ticker": "GOOD"},
        sample("market_settled.json") | {"ticker": "BAD", "volume_fp": "1.001"},
        sample("market_settled.json") | {"ticker": "ALSO-GOOD"},
    ]
    rig = Rig(lambda r: ok({"markets": rows, "cursor": ""}))
    (page,) = [p async for p in rig.data.markets(tolerant=True)]
    assert [m.ticker for m in page.items] == ["GOOD", "ALSO-GOOD"]
    assert [r["ticker"] for r in page.raw] == ["GOOD", "ALSO-GOOD"]  # raw lines up with items
    assert [raw["ticker"] for raw, _ in page.invalid] == ["BAD"]
    assert "BAD" in page.invalid[0][1] and "volume_fp" in page.invalid[0][1]


async def test_a_listing_with_nothing_wrong_has_no_invalid_rows(sample: Sample) -> None:
    rig = Rig(paged(sample, [["A", "B"]]))
    (page,) = [p async for p in rig.data.markets(tolerant=True)]
    assert page.invalid == [] and [m.ticker for m in page.items] == ["A", "B"]


async def test_live_markets_pass_the_documented_filters(sample: Sample) -> None:
    rig = Rig(paged(sample, [["A"]]))
    _ = [
        p
        async for p in rig.data.markets(
            status="settled",
            limit=500,
            mve_filter="exclude",
            tickers="A,B",
            min_settled_ts=1_000,
            max_settled_ts=2_000,
        )
    ]
    params = rig.requests[0].url.params
    assert params["status"] == "settled"
    assert params["limit"] == "500"
    assert params["min_settled_ts"] == "1000"
    assert params["max_settled_ts"] == "2000"
    assert params["tickers"] == "A,B"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"status": "pending"}, "status must be one of"),
        ({"mve_filter": "all"}, "mve_filter must be one of"),
        ({"limit": 0}, "limit must be between"),
        ({"limit": 1001}, "limit must be between"),
        ({"limit": True}, "limit must be between"),
        ({"min_settled_ts": 1, "min_close_ts": 1, "status": None}, "different groups"),
        ({"min_settled_ts": 1, "status": "open"}, "settled timestamp filters need status"),
        ({"min_close_ts": 1, "status": "settled"}, "close timestamp filters need status"),
        ({"min_created_ts": 1, "status": "closed"}, "created timestamp filters need status"),
        ({"min_updated_ts": 1, "status": "open"}, "updated timestamp filters need status"),
        ({"min_updated_ts": 1, "series_ticker": "KXDEMO"}, "requires mve_filter='exclude'"),
        ({"min_settled_ts": 5, "max_settled_ts": 4, "status": "settled"}, "is after"),
        ({"min_settled_ts": -1, "status": "settled"}, "non-negative integer"),
        ({"min_settled_ts": True, "status": "settled"}, "non-negative integer"),
        ({"min_bogus_ts": 1}, "unknown timestamp filter"),
    ],
)
def test_live_market_parameters_are_checked_before_anything_is_sent(
    kwargs: dict[str, Any], message: str
) -> None:
    rig = Rig(lambda r: ok({}))
    with pytest.raises(ParameterError, match=message):
        rig.data.markets(**kwargs)
    assert rig.requests == []


def test_updated_timestamps_are_allowed_with_an_excluded_combo_filter() -> None:
    rig = Rig(lambda r: ok({"markets": [], "cursor": ""}))
    rig.data.markets(min_updated_ts=10, series_ticker="KXDEMO", mve_filter="exclude")  # no error


def test_historical_market_parameters_are_checked() -> None:
    rig = Rig(lambda r: ok({}))
    with pytest.raises(ParameterError, match="mve_filter"):
        rig.data.historical_markets(mve_filter="maybe")
    with pytest.raises(ParameterError, match="limit"):
        rig.data.historical_markets(limit=2000)


async def test_trades_page_and_pass_their_filters(sample: Sample) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return ok({"trades": [sample("trade.json")], "cursor": ""})

    rig = Rig(handler)
    live = [
        p
        async for p in rig.data.trades(
            ticker="KXDEMO-26JAN01-T50", min_ts=100, max_ts=200, limit=50
        )
    ]
    assert live[0].items[0].count_e2 == 1_250
    assert live[0].raw[0]["trade_id"].startswith("00000000")
    request = rig.last
    assert request.url.path.endswith("/markets/trades")
    assert (request.url.params["min_ts"], request.url.params["max_ts"]) == ("100", "200")
    assert request.url.params["limit"] == "50"
    assert "is_block_trade" not in request.url.params
    _ = [p async for p in rig.data.historical_trades(is_block_trade=False)]
    assert rig.last.url.path.endswith("/historical/trades")
    assert rig.last.url.params["is_block_trade"] == "false"
    _ = [p async for p in rig.data.historical_trades(is_block_trade=True)]
    assert rig.last.url.params["is_block_trade"] == "true"


@pytest.mark.parametrize(
    "kwargs", [{"min_ts": 5, "max_ts": 4}, {"min_ts": -1}, {"max_ts": 1.5}, {"limit": 0}]
)
def test_trade_parameters_are_checked(kwargs: dict[str, Any]) -> None:
    rig = Rig(lambda r: ok({}))
    with pytest.raises(ParameterError):
        rig.data.trades(**kwargs)
    with pytest.raises(ParameterError):
        rig.data.historical_trades(**kwargs)
    assert rig.requests == []


# --- candlesticks -------------------------------------------------------------------------------


async def test_candlesticks_from_both_endpoints(sample: Sample) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        shape = (
            "candle_historical.json" if "/historical/" in request.url.path else "candle_live.json"
        )
        return ok({"ticker": "KXDEMO-26JAN01-T50", "candlesticks": [sample(shape)]})

    rig = Rig(handler)
    old = await rig.data.historical_candlesticks(
        "KXDEMO-26JAN01-T50", start_ts=10, end_ts=20, period_interval=60
    )
    assert rig.last.url.path.endswith("/historical/markets/KXDEMO-26JAN01-T50/candlesticks")
    assert (rig.last.url.params["start_ts"], rig.last.url.params["period_interval"]) == ("10", "60")
    new = await rig.data.candlesticks(
        "KXDEMO", "KXDEMO-26JAN01-T50", start_ts=10, end_ts=20, period_interval=1
    )
    assert rig.last.url.path.endswith("/series/KXDEMO/markets/KXDEMO-26JAN01-T50/candlesticks")
    assert old == new


@pytest.mark.parametrize("interval", [0, 2, 30, 120, 1441, True])
async def test_only_the_documented_candle_intervals_are_allowed(interval: int) -> None:
    rig = Rig(lambda r: ok({}))
    with pytest.raises(ParameterError, match="period_interval"):
        await rig.data.historical_candlesticks("T", start_ts=1, end_ts=2, period_interval=interval)
    assert rig.requests == []


async def test_every_other_interval_is_refused() -> None:
    rig = Rig(lambda r: ok({"candlesticks": []}))
    refused = 0
    for interval in range(0, 1501):
        if interval in (1, 60, 1440):
            continue
        with pytest.raises(ParameterError, match="period_interval"):
            await rig.data.candlesticks("S", "T", start_ts=1, end_ts=2, period_interval=interval)
        refused += 1
    assert refused == 1498
    assert rig.requests == []


async def test_candle_times_are_checked_and_the_response_must_be_a_list() -> None:
    rig = Rig(lambda r: ok({"candlesticks": "none"}))
    with pytest.raises(ParameterError, match="is after"):
        await rig.data.candlesticks("S", "T", start_ts=5, end_ts=4, period_interval=1)
    with pytest.raises(KalshiResponseError, match="candlesticks"):
        await rig.data.candlesticks("S", "T", start_ts=4, end_ts=5, period_interval=1440)


async def test_a_ticker_that_could_change_the_path_is_refused_by_the_client() -> None:
    from ikars_core.rest import ReadOnlyViolation

    rig = Rig(lambda r: ok({}))
    with pytest.raises(ReadOnlyViolation):
        await rig.data.market("../portfolio/balance")
    assert rig.requests == []
