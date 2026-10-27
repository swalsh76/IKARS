"""Environments and client settings: the one place that knows Kalshi's addresses.

Demo is the default. Production is an explicit opt-in (``IKARS_KALSHI_ENV=production`` or passing
``Environment.PRODUCTION``), and every client logs a warning when it is used.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class ConfigError(ValueError):
    """A setting is missing, malformed or out of range."""


class Environment(StrEnum):
    DEMO = "demo"
    PRODUCTION = "production"


REST_URLS: dict[Environment, str] = {
    Environment.DEMO: "https://external-api.demo.kalshi.co/trade-api/v2",
    Environment.PRODUCTION: "https://external-api.kalshi.com/trade-api/v2",
}
# the demo address is from Kalshi's demo-environment page (checked 2026-10-26); production was
# used live from K1a.5
WS_URLS: dict[Environment, str] = {
    Environment.DEMO: "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2",
    Environment.PRODUCTION: "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
}

# Measured live (BUILD-LOG K0.5.3c and K0.5.4): the historical trades endpoint held 10 requests a
# second for a full minute, but the live trades endpoint and both markets listings answered 429
# after a burst of about 12 and then allowed only about 4 a second (4 held for 30 seconds).
DEFAULT_RATE_LIMIT_PER_SECOND = 10.0  # historical trades; PLAN.md D3
DEFAULT_LIVE_RATE_LIMIT_PER_SECOND = 3.5  # live trades and the markets listings; PLAN.md D3
DEFAULT_REQUEST_COST_TOKENS = 10  # Kalshi's default cost of one request, in tokens


@dataclass(frozen=True, slots=True)
class KalshiConfig:
    """Settings for the read client. The defaults are the conservative ones from PLAN.md D3."""

    environment: Environment = Environment.DEMO
    rate_limit_per_second: float = DEFAULT_RATE_LIMIT_PER_SECOND  # requests, at the default cost
    burst_requests: float = DEFAULT_RATE_LIMIT_PER_SECOND  # one second of budget
    request_cost_tokens: int = DEFAULT_REQUEST_COST_TOKENS
    timeout_seconds: float = 30.0
    max_retries: int = 5
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8.0
    user_agent: str = "ikars/0"

    def __post_init__(self) -> None:
        positive = {
            "rate_limit_per_second": self.rate_limit_per_second,
            "burst_requests": self.burst_requests,
            "timeout_seconds": self.timeout_seconds,
            "backoff_base_seconds": self.backoff_base_seconds,
            "backoff_max_seconds": self.backoff_max_seconds,
        }
        for name, value in positive.items():
            if not value > 0:
                raise ConfigError(f"{name} must be positive, got {value!r}")
        if self.request_cost_tokens < 1:
            raise ConfigError("request_cost_tokens must be at least 1")
        if self.max_retries < 0:
            raise ConfigError("max_retries must not be negative")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ConfigError("backoff_max_seconds must be at least backoff_base_seconds")
        if self.burst_requests < 1:
            raise ConfigError("burst_requests must allow at least one request")

    @property
    def rest_url(self) -> str:
        return REST_URLS[self.environment]

    @property
    def ws_url(self) -> str:
        return WS_URLS[self.environment]

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @classmethod
    def at_rate(cls, rate: float, environment: Environment = Environment.DEMO) -> "KalshiConfig":
        """A client paced at ``rate`` requests a second, with a second's worth of burst (at least
        one request, so a rate below one a second is valid too)."""
        return cls(
            environment=environment, rate_limit_per_second=rate, burst_requests=max(rate, 1.0)
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "KalshiConfig":
        """Settings from ``IKARS_KALSHI_ENV`` (demo or production) and
        ``IKARS_RATE_LIMIT_PER_SECOND``."""
        env = os.environ if environ is None else environ
        raw_env = env.get("IKARS_KALSHI_ENV", Environment.DEMO.value).strip().lower()
        try:
            environment = Environment(raw_env)
        except ValueError:
            raise ConfigError(
                f"IKARS_KALSHI_ENV must be 'demo' or 'production', got {raw_env!r}"
            ) from None
        raw_rate = env.get("IKARS_RATE_LIMIT_PER_SECOND")
        if raw_rate is None:
            return cls(environment=environment)
        try:
            rate = float(raw_rate)
        except ValueError:
            raise ConfigError(
                f"IKARS_RATE_LIMIT_PER_SECOND must be a number, got {raw_rate!r}"
            ) from None
        return cls.at_rate(rate, environment)
