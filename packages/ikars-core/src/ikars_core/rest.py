"""Async, read-only REST clients for Kalshi: public market data, and the account reads.

Read-only is enforced twice and structurally: no write method exists, and a transport-level guard
refuses any request that is not a GET to the configured host and to an allowed path. The public
client (unsigned) allows the market-data families (``/markets``, ``/series``, ``/events``,
``/exchange`` and the public ``/historical`` reads); the account client signs with the account
reader and allows exactly the account reads. The trading client, when it exists (M7), is a separate
class with its own key; neither of these ever sends an order.

Every wait is bounded: each attempt has a timeout, retries have a limit and backoff is capped.
JSON is read with floats kept as text, so a multiplier such as ``0.5`` never becomes a binary float.
"""

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self
from urllib.parse import urlparse

import httpx

from ikars_core.auth import ACCOUNT_READS, API_PREFIX, AccountSigner, public_route
from ikars_core.config import KalshiConfig
from ikars_core.ratelimit import TokenBucket, backoff

log = logging.getLogger("ikars_core")

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class KalshiError(Exception):
    """Base class for every error this client raises."""


class KalshiAPIError(KalshiError):
    """Kalshi answered with an error status (after any retries)."""

    def __init__(self, status: int, body: str, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(f"Kalshi API error {status}: {body[:200]}")
        self.status = status
        self.body = body
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class KalshiConnectionError(KalshiError):
    """The request could not be completed (after any retries)."""


class KalshiResponseError(KalshiError):
    """Kalshi answered 200 but the body was not what the client can use."""


class ReadOnlyViolation(KalshiError):
    """A request was refused because it is not a GET to an allowed path on the configured host."""


def path_allowed(path: str) -> bool:
    """Whether ``path`` (relative to the API base, no query string) is a public market-data read:
    the same table the data key's signer uses (:func:`ikars_core.auth.public_route`)."""
    return public_route(API_PREFIX + path)


def account_path_allowed(path: str) -> bool:
    """Whether ``path`` (relative to the API base, no query string) is one of the account reads."""
    return path in ACCOUNT_READS


class Signer(Protocol):
    def headers(self, method: str, path: str) -> dict[str, str]: ...


def _guard(
    base_host: str, base_path: str, allowed: Callable[[str], bool]
) -> Callable[[httpx.Request], Awaitable[None]]:
    async def check(request: httpx.Request) -> None:
        target = f"{request.method} {request.url.host}{request.url.path}"
        if request.method != "GET":
            raise ReadOnlyViolation(f"{target} blocked: the client is read-only")
        if request.url.host != base_host or not request.url.path.startswith(base_path + "/"):
            raise ReadOnlyViolation(f"{target} blocked: not the configured API")
        if not allowed(request.url.path[len(base_path) :]):
            raise ReadOnlyViolation(f"{target} blocked: not an allowed read path")

    return check


@dataclass(slots=True)
class ClientStats:
    """Counters for progress reporting and for measuring the rate limit."""

    requests: int = 0  # every attempt that was sent
    retries: int = 0
    rate_limited: int = 0  # attempts answered 429
    server_errors: int = 0  # attempts answered 5xx
    transport_errors: int = 0


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a cursor-paged listing."""

    items: list[dict[str, Any]]
    request_cursor: str | None  # the cursor this page was fetched with (None for the first page)
    next_cursor: str | None  # the cursor for the next page; None when this was the last


class KalshiPublicClient:
    def __init__(
        self,
        config: KalshiConfig,
        *,
        bucket: TokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = lambda: random.uniform(0.5, 1.0),  # noqa: S311 - not security
        allowed: Callable[[str], bool] = path_allowed,
        signer: Signer | None = None,
        observe: Callable[[float, float, Mapping[str, str]], None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._allowed = allowed
        self._signer = signer
        self._observe = observe  # (sent, received, headers) of each reply, for the clock check
        self._clock = clock
        parsed = urlparse(config.rest_url)
        self._base = config.rest_url
        self._base_path = parsed.path.rstrip("/")
        self._bucket = bucket or TokenBucket(
            rate=config.rate_limit_per_second * config.request_cost_tokens,
            capacity=config.burst_requests * config.request_cost_tokens,
        )
        self._sleep = sleep
        self._jitter = jitter
        self.stats = ClientStats()
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=config.timeout_seconds,
            headers={"User-Agent": config.user_agent, "Accept": "application/json"},
            event_hooks={"request": [_guard(parsed.hostname or "", self._base_path, allowed)]},
            follow_redirects=False,
        )
        if config.is_production:
            log.warning("Kalshi client using the PRODUCTION environment (read-only)")

    @property
    def bucket(self) -> TokenBucket:
        return self._bucket

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _backoff(self, attempt: int) -> float:
        cfg = self._config
        return backoff(attempt, cfg.backoff_base_seconds, cfg.backoff_max_seconds) * self._jitter()

    async def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` (relative to the API base) and return the JSON object.

        Parameters set to ``None`` are left out. Retries 429, 5xx and transport errors up to
        ``max_retries`` times, each attempt paced by the rate limiter and each retry preceded by a
        capped, jittered backoff.
        """
        if not self._allowed(path):
            raise ReadOnlyViolation(f"GET {path} blocked: not an allowed read path")
        query = {k: v for k, v in (params or {}).items() if v is not None}
        cfg = self._config
        for attempt in range(cfg.max_retries + 1):
            await self._bucket.acquire(cfg.request_cost_tokens)
            self.stats.requests += 1
            try:
                # signed afresh on every attempt, so a retry never reuses an old timestamp
                headers = (
                    self._signer.headers("GET", self._base_path + path) if self._signer else None
                )
                sent = self._clock()
                response = await self._http.get(self._base + path, params=query, headers=headers)
                if self._observe is not None:
                    self._observe(sent, self._clock(), response.headers)
            except httpx.TransportError as exc:
                self.stats.transport_errors += 1
                if attempt == cfg.max_retries:
                    raise KalshiConnectionError(f"GET {path} failed: {exc!r}") from exc
            else:
                if response.status_code == 200:
                    return self._decode(path, response)
                if response.status_code == 429:
                    self.stats.rate_limited += 1
                elif response.status_code >= 500:
                    self.stats.server_errors += 1
                if response.status_code not in _RETRY_STATUSES or attempt == cfg.max_retries:
                    raise KalshiAPIError(response.status_code, response.text, response.headers)
            self.stats.retries += 1
            await self._sleep(self._backoff(attempt))
        raise AssertionError("unreachable")  # pragma: no cover - the loop always returns or raises

    @staticmethod
    def _decode(path: str, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = json.loads(response.content, parse_float=str)
        except ValueError as exc:
            raise KalshiResponseError(f"GET {path}: the body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise KalshiResponseError(f"GET {path}: expected a JSON object")
        return payload

    async def payloads(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        cursor: str | None = None,
    ) -> AsyncIterator[tuple[dict[str, Any], str | None, str | None]]:
        """Follow Kalshi's cursor paging, yielding each whole response with the cursors on either
        side. The one paging loop: :meth:`pages` and readers of responses with several lists use it.

        Start from ``cursor`` to resume a listing that was interrupted. A cursor that is not text,
        or one that repeats, is an error (the listing would never end).
        """
        seen: set[str] = set()
        while True:
            payload = await self.get_json(path, {**(params or {}), "cursor": cursor})
            next_cursor = payload.get("cursor") or None
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise KalshiResponseError(f"GET {path}: the cursor is not text")
            yield payload, cursor, next_cursor
            if next_cursor is None:
                return
            if next_cursor == cursor or next_cursor in seen:
                raise KalshiResponseError(
                    f"GET {path}: the cursor repeated; the listing would not end"
                )
            seen.add(next_cursor)
            cursor = next_cursor

    async def pages(
        self,
        path: str,
        items_key: str,
        params: Mapping[str, Any] | None = None,
        *,
        cursor: str | None = None,
    ) -> AsyncIterator[Page]:
        """Each page of the list under ``items_key``, with the cursors on either side."""
        async for payload, before, after in self.payloads(path, params, cursor=cursor):
            items = payload.get(items_key)
            if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
                raise KalshiResponseError(f"GET {path}: {items_key!r} is not a list of objects")
            yield Page(items, before, after)


class KalshiAccountClient(KalshiPublicClient):
    """The same GET-only client, signing with the account reader and limited to exactly the account
    reads (:data:`ikars_core.auth.ACCOUNT_READS`): no market data, no order route, no write."""

    def __init__(
        self,
        config: KalshiConfig,
        signer: AccountSigner,
        *,
        bucket: TokenBucket | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        observe: Callable[[float, float, Mapping[str, str]], None] | None = None,
    ) -> None:
        if not isinstance(signer, AccountSigner):
            raise TypeError("the account client signs with an AccountSigner only")
        super().__init__(
            config,
            bucket=bucket,
            transport=transport,
            sleep=sleep,
            allowed=account_path_allowed,
            signer=signer,
            observe=observe,
        )
