import pytest
from ikars_core.config import ConfigError, Environment, KalshiConfig


def test_demo_is_the_default() -> None:
    config = KalshiConfig()
    assert config.environment is Environment.DEMO
    assert not config.is_production
    assert "demo" in config.rest_url


def test_production_is_an_explicit_choice() -> None:
    config = KalshiConfig(environment=Environment.PRODUCTION)
    assert config.is_production
    assert config.rest_url.startswith("https://")
    assert "demo" not in config.rest_url


def test_each_environment_has_its_websocket_address_beside_its_rest_one() -> None:
    demo, production = KalshiConfig(), KalshiConfig(environment=Environment.PRODUCTION)
    assert demo.ws_url == "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    assert production.ws_url == "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    for config in (demo, production):  # the WebSocket host is the REST host with "-ws"
        rest_host = config.rest_url.split("/")[2]
        assert config.ws_url.split("/")[2] == rest_host.replace("external-api", "external-api-ws")


@pytest.mark.parametrize(("rate", "burst"), [(2.0, 2.0), (1.5, 1.5), (0.5, 1.0)])
def test_a_client_at_a_rate_has_a_seconds_burst_of_at_least_one_request(
    rate: float, burst: float
) -> None:
    config = KalshiConfig.at_rate(rate, Environment.PRODUCTION)
    assert (config.rate_limit_per_second, config.burst_requests) == (rate, burst)
    assert config.is_production and not KalshiConfig.at_rate(rate).is_production


def test_the_defaults_are_the_conservative_ones() -> None:
    config = KalshiConfig()
    assert config.rate_limit_per_second == 10.0
    assert config.burst_requests == 10.0
    assert config.request_cost_tokens == 10


@pytest.mark.parametrize(
    ("env", "expected"),
    [({}, Environment.DEMO), ({"IKARS_KALSHI_ENV": "production"}, Environment.PRODUCTION),
     ({"IKARS_KALSHI_ENV": " Production "}, Environment.PRODUCTION),
     ({"IKARS_KALSHI_ENV": "DEMO"}, Environment.DEMO)],
)  # fmt: skip
def test_the_environment_comes_from_kalshi_env(env: dict[str, str], expected: Environment) -> None:
    assert KalshiConfig.from_env(env).environment is expected


def test_an_unknown_environment_is_refused() -> None:
    with pytest.raises(ConfigError, match="IKARS_KALSHI_ENV"):
        KalshiConfig.from_env({"IKARS_KALSHI_ENV": "staging"})


def test_the_rate_can_be_set_from_the_environment() -> None:
    config = KalshiConfig.from_env({"IKARS_RATE_LIMIT_PER_SECOND": "2.5"})
    assert config.rate_limit_per_second == 2.5
    assert config.burst_requests == 2.5
    assert KalshiConfig.from_env({"IKARS_RATE_LIMIT_PER_SECOND": "0.5"}).burst_requests == 1.0


@pytest.mark.parametrize("raw", ["fast", "", "0", "-1", "nan"])
def test_a_bad_rate_is_refused(raw: str) -> None:
    with pytest.raises(ConfigError):
        KalshiConfig.from_env({"IKARS_RATE_LIMIT_PER_SECOND": raw})


def test_from_env_reads_the_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IKARS_KALSHI_ENV", "production")
    monkeypatch.delenv("IKARS_RATE_LIMIT_PER_SECOND", raising=False)
    assert KalshiConfig.from_env().is_production


@pytest.mark.parametrize(
    "bad",
    [
        {"rate_limit_per_second": 0.0},
        {"timeout_seconds": 0.0},
        {"backoff_base_seconds": -1.0},
        {"burst_requests": 0.5},
        {"request_cost_tokens": 0},
        {"max_retries": -1},
        {"backoff_base_seconds": 4.0, "backoff_max_seconds": 1.0},
    ],
)
def test_out_of_range_settings_are_refused(bad: dict[str, float]) -> None:
    with pytest.raises(ConfigError):
        KalshiConfig(**bad)  # type: ignore[arg-type]
