"""Who may sign and fetch what: the data key never reaches account data, the account reader reaches
exactly the account reads (PLAN D18) and nothing else, and both are GET-only."""

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ikars_core.auth import (
    ACCOUNT_READS,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    AccountSigner,
    DataSigner,
    SigningRefused,
    account_route,
    read_route,
)
from ikars_core.config import Environment, KalshiConfig
from ikars_core.ratelimit import TokenBucket
from ikars_core.rest import (
    KalshiAccountClient,
    KalshiPublicClient,
    ReadOnlyViolation,
    account_path_allowed,
    path_allowed,
)

KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
CONFIG = KalshiConfig(environment=Environment.PRODUCTION)
ACCOUNT_HISTORY = ["/historical/fills", "/historical/orders", "/historical/positions"]


def clock(start: int = 1_000) -> Any:
    ticks = iter(range(start, start + 1_000))
    return lambda: next(ticks)


# --- the data key -------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [*ACCOUNT_HISTORY, "/historical/fills/x", "/portfolio/balance"])
def test_the_data_key_never_signs_account_data(path: str) -> None:
    assert not read_route("/trade-api/v2" + path)
    with pytest.raises(SigningRefused, match="data key does not sign"):
        DataSigner("k", KEY).headers("GET", "/trade-api/v2" + path)


@pytest.mark.parametrize(
    "path",
    ["/historical/cutoff", "/historical/markets", "/historical/markets/T-1/candlesticks",
     "/historical/trades", "/markets", "/account/limits"],
)  # fmt: skip
def test_the_data_key_still_signs_the_public_reads(path: str) -> None:
    assert read_route("/trade-api/v2" + path)


@pytest.mark.parametrize("path", [*ACCOUNT_HISTORY, "/historical/fillsx", "/historical"])
def test_the_public_client_cannot_reach_account_history(path: str) -> None:
    assert not path_allowed(path)


# --- the account reader -------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(ACCOUNT_READS))
def test_the_account_reader_signs_exactly_the_account_reads(path: str) -> None:
    assert account_route("/trade-api/v2" + path)
    assert account_path_allowed(path)
    headers = AccountSigner("k", KEY).headers("GET", "/trade-api/v2" + path + "?limit=5")
    assert headers[HEADER_SIGNATURE]


@pytest.mark.parametrize(
    "path",
    ["/portfolio/orders", "/portfolio/balance/x", "/portfolio/fills/../orders", "/portfolio",
     "/historical/orders", "/markets", "/exchange/status", "/portfolio/subaccounts/transfer",
     "/portfolio/order_groups", "/account", ""],
)  # fmt: skip
def test_the_account_reader_refuses_everything_else(path: str) -> None:
    assert not account_route("/trade-api/v2" + path)
    assert not account_path_allowed(path)
    with pytest.raises(SigningRefused, match="account reader does not sign"):
        AccountSigner("k", KEY).headers("GET", "/trade-api/v2" + path)


def test_the_account_reader_does_not_sign_the_websocket_or_another_api() -> None:
    assert not account_route("/trade-api/ws/v2")
    assert not account_route("/other-api/v2/portfolio/balance")


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_the_account_reader_signs_gets_only(method: str) -> None:
    with pytest.raises(SigningRefused, match="GET requests only"):
        AccountSigner("k", KEY).headers(method, "/trade-api/v2/portfolio/balance")


# --- the account client -------------------------------------------------------------------------


def account_client(handler: Any, seen: list[httpx.Request]) -> KalshiAccountClient:
    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        response: httpx.Response = handler(request)
        return response

    async def no_sleep(_: float) -> None:
        return None

    return KalshiAccountClient(
        CONFIG,
        AccountSigner("key-id", KEY, now_ms=clock()),
        bucket=TokenBucket(rate=1e9, capacity=1e9),
        transport=httpx.MockTransport(record),
        sleep=no_sleep,
    )


async def test_the_account_client_signs_each_attempt_afresh() -> None:
    replies = iter([httpx.Response(429), httpx.Response(200, content=json.dumps({"balance": 1}))])
    seen: list[httpx.Request] = []
    client = account_client(lambda _: next(replies), seen)
    assert await client.get_json("/portfolio/balance") == {"balance": 1}
    stamps = [r.headers[HEADER_TIMESTAMP] for r in seen]
    assert len(stamps) == 2 and stamps[0] != stamps[1]
    assert all(r.headers["KALSHI-ACCESS-KEY"] == "key-id" for r in seen)
    assert seen[0].url.path == "/trade-api/v2/portfolio/balance"


async def test_the_account_client_refuses_market_data_before_sending() -> None:
    seen: list[httpx.Request] = []
    client = account_client(lambda _: httpx.Response(200, content=b"{}"), seen)
    with pytest.raises(ReadOnlyViolation):
        await client.get_json("/markets")
    with pytest.raises(ReadOnlyViolation):
        await client.get_json("/portfolio/orders")
    assert seen == []


async def test_the_account_client_transport_guard_refuses_other_paths() -> None:
    seen: list[httpx.Request] = []
    client = account_client(lambda _: httpx.Response(200, content=b"{}"), seen)
    with pytest.raises(ReadOnlyViolation, match="not an allowed read path"):
        await client._http.get(CONFIG.rest_url + "/portfolio/orders")
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        await client._http.post(CONFIG.rest_url + "/portfolio/balance")
    assert seen == []


async def test_the_public_client_sends_no_signature() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"{}")

    client = KalshiPublicClient(
        CONFIG, bucket=TokenBucket(rate=1e9, capacity=1e9), transport=httpx.MockTransport(record)
    )
    await client.get_json("/markets")
    assert "KALSHI-ACCESS-SIGNATURE" not in seen[0].headers
