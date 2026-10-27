import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from ikars_core.config import Environment, KalshiConfig
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import (
    KalshiAPIError,
    KalshiConnectionError,
    KalshiPublicClient,
    KalshiResponseError,
    Page,
    ReadOnlyViolation,
    path_allowed,
)

CONFIG = KalshiConfig(environment=Environment.PRODUCTION)
Handler = Callable[[httpx.Request], httpx.Response]


class Harness:
    """A client over a scripted transport, a huge bucket and a sleep that only records."""

    def __init__(self, handler: Handler, config: KalshiConfig = CONFIG) -> None:
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

        def record(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if len(self.requests) > 100:  # a runaway loop must fail the test, not hang it
                raise AssertionError("more than 100 requests: the client did not stop")
            return handler(request)

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.client = KalshiPublicClient(
            config,
            bucket=TokenBucket(rate=1e9, capacity=1e9),
            transport=httpx.MockTransport(record),
            sleep=sleep,
            jitter=lambda: 1.0,
        )


def ok(body: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(body).encode())


def sequence(*responses: httpx.Response | Exception) -> Handler:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


# --- reading ------------------------------------------------------------------------------------


async def test_get_json_returns_the_object_and_sends_the_documented_headers() -> None:
    h = Harness(lambda r: ok({"markets": []}))
    assert await h.client.get_json(
        "/markets", {"limit": 5, "status": "settled", "cursor": None}
    ) == {"markets": []}
    request = h.requests[0]
    assert request.url.params["limit"] == "5"
    assert request.url.params["status"] == "settled"
    assert "cursor" not in request.url.params  # None parameters are left out
    assert request.headers["accept"] == "application/json"
    assert request.headers["user-agent"].startswith("ikars/")
    assert "kalshi-access-key" not in request.headers  # the public client never signs


async def test_floats_are_kept_as_text_and_integers_stay_integers() -> None:
    body = b'{"fee_multiplier": 0.5, "other": 1.10, "n": 3, "nested": {"x": 2.50}}'
    h = Harness(lambda r: httpx.Response(200, content=body))
    payload = await h.client.get_json("/series/KXTEST")
    assert payload == {"fee_multiplier": "0.5", "other": "1.10", "n": 3, "nested": {"x": "2.50"}}


@pytest.mark.parametrize("content", [b"not json", b"", b"[1, 2]", b'"text"', b"null", b"3"])
async def test_a_200_that_is_not_a_json_object_is_an_error_and_is_not_retried(
    content: bytes,
) -> None:
    h = Harness(lambda r: httpx.Response(200, content=content))
    with pytest.raises(KalshiResponseError):
        await h.client.get_json("/exchange/status")
    assert len(h.requests) == 1


# --- read-only guard ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/markets", True),
        ("/markets/KXBTC-26AUG21-T50000", True),
        ("/markets/trades", True),
        ("/historical/cutoff", True),
        ("/historical/markets/KX_A.B-1/candlesticks", True),
        ("/series", True),
        ("/series/fee_changes", True),
        ("/events/KXE-1", True),
        ("/exchange/status", True),
        ("/portfolio/balance", False),
        ("/portfolio/orders", False),
        ("/account/limits", False),
        ("/", False),
        ("", False),
        ("markets", False),
        ("/markets/", False),
        ("/markets//trades", False),
        ("/markets/../portfolio/balance", False),
        ("/markets/./x", False),
        ("/marketsx", False),
        ("/markets?x=1", False),
        ("/markets/a b", False),
        ("/Markets", False),
    ],
)
def test_path_allowed(path: str, allowed: bool) -> None:
    assert path_allowed(path) is allowed


async def test_a_disallowed_path_is_refused_before_anything_is_sent() -> None:
    h = Harness(lambda r: ok({}))
    with pytest.raises(ReadOnlyViolation, match="not an allowed read path"):
        await h.client.get_json("/portfolio/balance")
    assert h.requests == []


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
async def test_the_transport_refuses_anything_but_get(method: str) -> None:
    h = Harness(lambda r: ok({}))
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        await h.client._http.request(method, CONFIG.rest_url + "/markets")
    assert h.requests == []


async def test_the_transport_refuses_another_host_and_a_path_outside_the_api() -> None:
    h = Harness(lambda r: ok({}))
    with pytest.raises(ReadOnlyViolation, match="not the configured API"):
        await h.client._http.get("https://example.invalid/trade-api/v2/markets")
    with pytest.raises(ReadOnlyViolation, match="not the configured API"):
        await h.client._http.get("https://external-api.kalshi.com/other/markets")
    with pytest.raises(ReadOnlyViolation, match="not an allowed read path"):
        await h.client._http.get(CONFIG.rest_url + "/portfolio/orders")
    assert h.requests == []


# --- retries ------------------------------------------------------------------------------------


async def test_a_429_is_retried_after_a_doubling_backoff() -> None:
    h = Harness(sequence(httpx.Response(429), httpx.Response(429), ok({"ok": True})))
    assert await h.client.get_json("/exchange/status") == {"ok": True}
    assert h.sleeps == [0.5, 1.0]
    assert len(h.requests) == 3
    s = h.client.stats
    assert (s.requests, s.retries, s.rate_limited, s.server_errors) == (3, 2, 2, 0)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_server_errors_are_retried(status: int) -> None:
    h = Harness(sequence(httpx.Response(status), ok({"ok": True})))
    assert await h.client.get_json("/exchange/status") == {"ok": True}
    assert h.client.stats.server_errors == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_other_errors_are_not_retried(status: int) -> None:
    h = Harness(lambda r: httpx.Response(status, text="no such thing", headers={"X-Demo": "1"}))
    with pytest.raises(KalshiAPIError) as caught:
        await h.client.get_json("/markets/NOPE")
    assert caught.value.status == status
    assert caught.value.body == "no such thing"
    assert caught.value.headers["x-demo"] == "1"  # header names are lower-cased
    assert len(h.requests) == 1
    assert h.sleeps == []


async def test_retries_are_bounded() -> None:
    h = Harness(lambda r: httpx.Response(429, text="too many requests"))
    with pytest.raises(KalshiAPIError) as caught:
        await h.client.get_json("/markets")
    assert caught.value.status == 429
    assert len(h.requests) == CONFIG.max_retries + 1
    assert len(h.sleeps) == CONFIG.max_retries


async def test_the_backoff_is_capped() -> None:
    config = KalshiConfig(environment=Environment.PRODUCTION, max_retries=8)
    h = Harness(lambda r: httpx.Response(503), config)
    with pytest.raises(KalshiAPIError):
        await h.client.get_json("/markets")
    assert h.sleeps == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0, 8.0, 8.0]


async def test_the_backoff_is_jittered() -> None:
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = KalshiPublicClient(
        CONFIG,
        bucket=TokenBucket(1e9, 1e9),
        transport=httpx.MockTransport(sequence(httpx.Response(429), ok({}))),
        sleep=sleep,
        jitter=lambda: 0.5,
    )
    await client.get_json("/markets")
    assert sleeps == [0.25]


async def test_a_transport_error_is_retried_then_reported() -> None:
    h = Harness(sequence(httpx.ConnectError("down"), ok({"ok": True})))
    assert await h.client.get_json("/markets") == {"ok": True}
    assert h.client.stats.transport_errors == 1

    dead = Harness(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("slow")))
    with pytest.raises(KalshiConnectionError, match="GET /markets failed"):
        await dead.client.get_json("/markets")
    assert dead.client.stats.transport_errors == CONFIG.max_retries + 1


async def test_every_attempt_is_paced_by_the_rate_limiter() -> None:
    class Counting(TokenBucket):
        costs: list[float]

        async def acquire(self, cost: float) -> None:
            self.costs.append(cost)
            await super().acquire(cost)

    bucket = Counting(1e9, 1e9)
    bucket.costs = []

    async def sleep(seconds: float) -> None:
        return None

    client = KalshiPublicClient(
        CONFIG,
        bucket=bucket,
        transport=httpx.MockTransport(sequence(httpx.Response(429), ok({}))),
        sleep=sleep,
        jitter=lambda: 1.0,
    )
    await client.get_json("/markets")
    assert bucket.costs == [10, 10]  # the retry is paced too


async def test_the_default_bucket_follows_the_configured_rate() -> None:
    config = KalshiConfig(
        environment=Environment.PRODUCTION, rate_limit_per_second=2.0, burst_requests=2.0
    )
    client = KalshiPublicClient(config, transport=httpx.MockTransport(lambda r: ok({})))
    for _ in range(3):
        await client.get_json("/markets")
    assert client.bucket.stats.acquired == 3
    assert client.bucket.stats.waits == 1  # two free, the third waits for the refill
    await client.aclose()


# --- paging -------------------------------------------------------------------------------------


def paged(pages: list[dict[str, Any]]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor", "")
        index = {"": 0, "c1": 1, "c2": 2}[cursor]
        return ok(pages[index])

    return handler


async def collect(h: Harness, **kwargs: Any) -> list[Page]:
    return [page async for page in h.client.pages("/markets", "markets", **kwargs)]


async def test_pages_follow_the_cursor_to_the_end() -> None:
    h = Harness(
        paged(
            [
                {"markets": [{"ticker": "A"}, {"ticker": "B"}], "cursor": "c1"},
                {"markets": [{"ticker": "C"}], "cursor": "c2"},
                {"markets": [{"ticker": "D"}], "cursor": ""},
            ]
        )
    )
    pages = await collect(h)
    assert [[i["ticker"] for i in p.items] for p in pages] == [["A", "B"], ["C"], ["D"]]
    assert [(p.request_cursor, p.next_cursor) for p in pages] == [
        (None, "c1"),
        ("c1", "c2"),
        ("c2", None),
    ]
    assert "cursor" not in h.requests[0].url.params


async def test_pages_can_resume_from_a_saved_cursor() -> None:
    h = Harness(
        paged([{}, {"markets": [{"ticker": "C"}], "cursor": "c2"}, {"markets": [{"ticker": "D"}]}])
    )
    pages = await collect(h, cursor="c1")
    assert [i["ticker"] for p in pages for i in p.items] == ["C", "D"]
    assert pages[0].request_cursor == "c1"


async def test_pages_pass_the_other_parameters_on_every_request() -> None:
    h = Harness(paged([{"markets": [], "cursor": "c1"}, {"markets": [], "cursor": ""}, {}]))
    await collect(h, params={"status": "settled", "limit": 1000})
    assert all(
        r.url.params["status"] == "settled" and r.url.params["limit"] == "1000" for r in h.requests
    )


async def test_a_repeated_cursor_ends_with_an_error() -> None:
    same = Harness(lambda r: ok({"markets": [], "cursor": "c1"}))
    with pytest.raises(KalshiResponseError, match="repeated"):
        await collect(same)
    assert len(same.requests) == 2  # c1 is new on the first reply, repeated on the second

    loop = Harness(
        lambda r: ok(
            {
                "markets": [],
                "cursor": {"": "c1", "c1": "c2", "c2": "c1"}[r.url.params.get("cursor", "")],
            }
        )
    )
    with pytest.raises(KalshiResponseError, match="repeated"):
        await collect(loop)


async def test_resuming_with_the_cursor_the_server_returns_is_an_error() -> None:
    h = Harness(lambda r: ok({"markets": [], "cursor": "c1"}))
    with pytest.raises(KalshiResponseError, match="repeated"):
        await collect(h, cursor="c1")


@pytest.mark.parametrize(
    "body",
    [
        {"nope": []},
        {"markets": "x"},
        {"markets": [1]},
        {"markets": [], "cursor": 5},
        {"markets": None},
    ],
)
async def test_a_malformed_page_is_an_error(body: dict[str, Any]) -> None:
    h = Harness(lambda r: ok(body))
    with pytest.raises(KalshiResponseError):
        await collect(h)


# --- lifecycle ----------------------------------------------------------------------------------


async def test_the_client_is_an_async_context_manager() -> None:
    async with KalshiPublicClient(
        CONFIG, transport=httpx.MockTransport(lambda r: ok({}))
    ) as client:
        assert await client.get_json("/exchange/status") == {}
        assert not client._http.is_closed
    assert client._http.is_closed


def test_production_is_announced(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="ikars_core"):
        KalshiPublicClient(CONFIG, transport=httpx.MockTransport(lambda r: ok({})))
        KalshiPublicClient(KalshiConfig(), transport=httpx.MockTransport(lambda r: ok({})))
    messages = [r.getMessage() for r in caplog.records]
    assert sum("PRODUCTION" in m for m in messages) == 1  # demo says nothing
