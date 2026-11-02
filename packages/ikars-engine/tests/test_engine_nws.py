"""M2.2: NWS weather. The stations come from the markets' rules; the replies here are hand-written
and made up (the test-data policy), shaped like api.weather.gov's."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from ikars_core.models import Market
from ikars_engine.kalshi.discovery import MarketIndex, climate_code
from ikars_engine.model import Item
from ikars_engine.nws import (
    NwsWeather,
    parse_climate,
    parse_forecast,
    parse_observation,
    station_of,
)
from ikars_engine.plugins import PluginContext
from ikars_engine.polling import Poller, user_agent
from ikars_engine.store import StateStore

NS = 1_000_000_000
T0 = 1_767_225_600 * NS

CLIMATE_TODAY = """
000
CDUS41 KXYZ 011632
CLIXYZ

CLIMATE REPORT
NATIONAL WEATHER SERVICE MADE-UP TOWN
1132 AM EST THU JAN 01 2026

...THE MADE-UP TOWN CLIMATE SUMMARY FOR JANUARY 1 2026...
VALID TODAY AS OF 1100 AM LOCAL TIME.

WEATHER ITEM   OBSERVED TIME   RECORD YEAR NORMAL DEPARTURE LAST
TEMPERATURE (F)
 TODAY
  MAXIMUM         41    1046 AM  60    1950  40      1       35
  MINIMUM        -3    0605 AM -10    1960  28     -2       30

PRECIPITATION (IN)
  TODAY            T             1.20 1990   0.10  -0.10     0.00

SNOWFALL (IN)
  TODAY            0.4           3.0  1980   0.2    0.2      0.0
"""

CLIMATE_FINAL = (
    CLIMATE_TODAY.replace("VALID TODAY AS OF 1100 AM LOCAL TIME.\n", "")
    .replace(" TODAY\n  MAXIMUM         41", " YESTERDAY\n  MAXIMUM         MM")
    .replace("  TODAY            T ", "  YESTERDAY        0.12")
    .replace("  TODAY            0.4", "  YESTERDAY        MM ")
)


def made_up_market(ticker: str, rules: str | None, status: str = "active") -> Market:
    return Market.model_validate({
        "ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "status": status,
        "title": "Made-up high", "rules_primary": rules,
    })  # fmt: skip


def test_the_stations_come_from_the_markets_rules() -> None:
    assert climate_code("recorded at Made-up Town (CLIXYZ) for Jan 1") == "CLIXYZ"
    assert climate_code("no station here") is None and climate_code(None) is None
    assert station_of("CLIXYZ") == "KXYZ"
    index = MarketIndex()
    index.put(made_up_market("KXHIGHXYZ-26JAN01-T40", "recorded at Made-up Town (CLIXYZ)"))
    index.put(made_up_market("KXLOWXYZ-26JAN01-T20", "the minimum at Made-up Town (CLIXYZ)"))
    index.put(made_up_market("KXHIGHABC-26JAN01-T50", "at Other Town (CLIABC)", "settled"))
    index.put(made_up_market("KXOTHER-26JAN01", "nothing about weather"))
    assert index.climate_codes() == {"CLIXYZ": ["KXHIGHXYZ-26JAN01-T40", "KXLOWXYZ-26JAN01-T20"]}
    index.put(made_up_market("KXHIGHXYZ-26JAN01-T40", None))  # an update without its rules
    assert index.markets["KXHIGHXYZ-26JAN01-T40"].climate_code == "CLIXYZ"


def test_an_observation_is_kept_as_nws_gives_it() -> None:
    body = json.dumps({"properties": {
        "timestamp": "2026-01-01T15:51:00+00:00", "textDescription": "Clear",
        "temperature": {"value": 5.0, "unitCode": "wmoUnit:degC"},
        "dewpoint": {"value": None, "unitCode": "wmoUnit:degC"},
    }}).encode()  # fmt: skip
    [found] = parse_observation("KXYZ", body)
    assert found.id == "KXYZ@2026-01-01T15:51:00+00:00"
    assert found.published_ns == 1_767_282_660 * NS
    assert found.payload["temperature"] == {"value": 5.0, "unit": "wmoUnit:degC"}
    assert found.payload["dewpoint"] is None  # NWS sent no value
    assert parse_observation("KXYZ", b'{"properties": {}}') == []


def test_a_forecast_keeps_its_first_two_days() -> None:
    periods = [{"startTime": f"p{n}", "temperature": n, "temperatureUnit": "F",
                "probabilityOfPrecipitation": {"value": 10, "unitCode": "wmoUnit:percent"}}
               for n in range(156)]  # fmt: skip
    body = json.dumps({"properties": {"updateTime": "2026-01-01T12:00:00+00:00",
                                      "periods": periods}}).encode()  # fmt: skip
    [hourly] = parse_forecast("KXYZ", "hourly", body)
    assert hourly.id == "KXYZ:hourly@2026-01-01T12:00:00+00:00"
    assert len(hourly.payload["periods"]) == 48
    assert hourly.payload["periods"][0]["rain_chance"] == {"value": 10, "unit": "wmoUnit:percent"}
    [daily] = parse_forecast("KXYZ", "daily", body)
    assert len(daily.payload["periods"]) == 156


def product(text: str, pid: str = "made-up-1") -> bytes:
    return json.dumps({"id": pid, "issuanceTime": "2026-01-01T16:32:00+00:00",
                       "issuingOffice": "KXYZ", "productText": text}).encode()  # fmt: skip


def test_the_climate_report_is_read_today_and_final() -> None:
    [today] = parse_climate("CLIXYZ", product(CLIMATE_TODAY))
    p = today.payload
    assert (p["day"], p["span"], p["valid_as_of"]) == ("JANUARY 1 2026", "today", "1100 AM")
    assert (p["high_f"], p["low_f"], p["precip_in"], p["snow_in"]) == (41, -3, "T", "0.4")
    assert today.id == "made-up-1" and p["text"] == CLIMATE_TODAY
    [final] = parse_climate("CLIXYZ", product(CLIMATE_FINAL, "made-up-2"))
    f = final.payload
    assert (f["span"], f["valid_as_of"], f["high_f"], f["low_f"]) == ("yesterday", None, None, -3)
    assert (f["precip_in"], f["snow_in"]) == ("0.12", None)  # MM: missing, not zero
    assert parse_climate("CLIXYZ", b'{"id": "x"}') == []


class FakeClock:
    def __init__(self, sleeps: int) -> None:
        self.now, self._left = T0, sleeps

    def now_ns(self) -> int:
        return self.now

    def monotonic_ns(self) -> int:
        return self.now

    async def sleep(self, seconds: float) -> None:
        if self._left == 0:
            raise asyncio.CancelledError
        self._left -= 1
        self.now += int(seconds * NS)
        await asyncio.sleep(0)


def nws(request: httpx.Request) -> httpx.Response:
    """A made-up api.weather.gov: Made-up Town maps; Nowhere (CLINOP) has no station."""
    path = request.url.path
    geo: dict[str, Any]
    if path == "/stations/KXYZ":
        geo = {"geometry": {"coordinates": [-73.123456, 40.654321]}}
    elif path == "/points/40.6543,-73.1235":
        geo = {"properties": {
            "gridId": "XYZ", "gridX": 1, "gridY": 2,
            "forecast": "https://api.weather.gov/gridpoints/XYZ/1,2/forecast",
            "forecastHourly": "https://api.weather.gov/gridpoints/XYZ/1,2/forecast/hourly",
        }}  # fmt: skip
    elif path == "/stations/KXYZ/observations/latest":
        geo = {"properties": {"timestamp": "2026-01-01T15:51:00+00:00"}}
    elif path.startswith("/gridpoints/XYZ/1,2/forecast"):
        geo = {"properties": {"updateTime": "2026-01-01T12:00:00+00:00", "periods": []}}
    elif path == "/products/types/CLI/locations/XYZ/latest":
        return httpx.Response(200, content=product(CLIMATE_TODAY))
    else:
        return httpx.Response(404)
    return httpx.Response(200, json=geo)


def test_the_source_maps_its_stations_and_records_each_feed(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite")
    clock = FakeClock(sleeps=8)
    seen: list[str] = []

    def recorded(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return nws(request)

    poller = Poller(store, clock, user_agent("9.9"), transport=httpx.MockTransport(recorded))
    emitted: list[Item] = []

    async def emit(item: Item) -> None:
        emitted.append(item)

    source = NwsWeather()
    services = {"poller": lambda: poller, "climate_codes": lambda: ["CLINOP", "CLIXYZ"]}
    ctx = PluginContext(source.spec, clock, {}, emit, 30.0, services=services)

    async def go() -> None:
        try:
            await source.run(ctx)
        except (asyncio.CancelledError, ExceptionGroup):
            pass
        finally:
            await poller.aclose()

    asyncio.run(go())
    kinds = sorted({i.ref.kind for i in emitted})
    assert kinds == ["weather_climate", "weather_forecast", "weather_obs"]
    assert {i.payload["feed"] for i in emitted} == {
        "nws.KXYZ.obs", "nws.KXYZ.hourly", "nws.KXYZ.daily", "nws.CLIXYZ.cli"
    }  # fmt: skip
    note = poller.notes["nws"]
    assert note["mapped"] == ["CLIXYZ"] and note["unmapped"] == {"CLINOP": "HTTP 404"}
    assert "/points/40.6543,-73.1235" in seen  # four places, as NWS asks
    store.close()


def test_the_source_can_be_turned_off(tmp_path: Path) -> None:
    async def emit(item: Item) -> None:
        raise AssertionError

    source = NwsWeather()
    store = StateStore(tmp_path / "state.sqlite")
    poller = Poller(store, FakeClock(0), user_agent("9.9"), transport=httpx.MockTransport(nws))
    ctx = PluginContext(
        source.spec, FakeClock(0), {"enabled": False}, emit, 30.0,
        services={"poller": lambda: poller, "climate_codes": lambda: ["CLIXYZ"]},
    )  # fmt: skip
    asyncio.run(source.run(ctx))
    asyncio.run(poller.aclose())
    store.close()


def test_a_city_is_retried_then_stopped_when_its_markets_close(tmp_path: Path) -> None:
    # per-commit review: a lookup that failed is tried again next hour; closed markets stop feeds
    store = StateStore(tmp_path / "state.sqlite")
    clock = FakeClock(sleeps=60)
    failed_once: list[bool] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/stations/KXYZ" and not failed_once:
            failed_once.append(True)
            return httpx.Response(503)
        return nws(request)

    poller = Poller(store, clock, user_agent("9.9"), transport=httpx.MockTransport(flaky))
    hours = [["CLIXYZ"], ["CLIXYZ"], []]  # the third hour: its markets have closed
    states: list[tuple[list[str], dict[str, str], int]] = []

    def codes() -> list[str]:
        note = poller.notes.get("nws", {"mapped": [], "unmapped": {}})
        states.append((list(note["mapped"]), dict(note["unmapped"]), len(asyncio.all_tasks())))
        return hours.pop(0) if hours else []

    async def emit(item: Item) -> None:
        return None

    source = NwsWeather()
    ctx = PluginContext(source.spec, clock, {}, emit, 30.0,
                        services={"poller": lambda: poller, "climate_codes": codes})  # fmt: skip

    async def go() -> None:
        try:
            await source.run(ctx)
        except (asyncio.CancelledError, ExceptionGroup):
            pass
        finally:
            await poller.aclose()

    asyncio.run(go())
    assert states[1][:2] == ([], {"CLIXYZ": "HTTP 503"})  # the first hour: not mapped
    assert states[2][0] == ["CLIXYZ"] and states[2][1] == {}  # the second: mapped, feeds started
    assert states[3][:2] == ([], {})  # the third: closed, its feeds stopped
    assert states[3][2] < states[2][2]  # fewer tasks: the four feed loops ended
    assert not [n for n in poller.feeds if n.startswith("nws.")]  # and left health (review 3)
    store.close()


def test_a_record_high_and_a_blank_row_are_read_as_they_are() -> None:
    # merge review 2 and 4
    record = CLIMATE_TODAY.replace("MAXIMUM         41 ", "MAXIMUM         92R").replace(
        "  TODAY            T             1.20", "  TODAY           \n  MONTH TO DATE    1.23"
    )
    [found] = parse_climate("CLIXYZ", product(record))
    assert (found.payload["high_f"], found.payload["high_record"]) == (92, True)
    assert (found.payload["low_f"], found.payload["low_record"]) == (-3, False)
    assert found.payload["precip_in"] is None  # never "MONTH" from the next line


def test_times_are_exact_and_never_guessed() -> None:
    # merge review 9
    from ikars_engine.nws import _ns

    assert _ns("2026-01-01T15:51:00.123456+00:00") == 1_767_282_660_123_456_000
    assert _ns("2026-01-01T15:51:00") is None  # no offset: not read as local time
    assert _ns("not a time") is None and _ns(None) is None


def test_revised_rules_clear_a_stale_code_and_only_codes_are_listed() -> None:
    # merge review 6 and 7
    index = MarketIndex()
    index.put(made_up_market("KXHIGHXYZ-26JAN01-T40", "at Made-up Town (CLIXYZ)"))
    index.put(made_up_market("KXHIGHABC-26JAN01-T40", "at Other Town (CLIABC)"))
    assert index.open_climate_codes() == ["CLIABC", "CLIXYZ"]
    index.put(made_up_market("KXHIGHXYZ-26JAN01-T40", "rules revised, no station named"))
    assert index.open_climate_codes() == ["CLIABC"]


def test_a_slow_lookup_leaves_the_city_unmapped_not_the_source_crashed(tmp_path: Path) -> None:
    # merge review 1: a lookup that times out is tried again; the other cities go on
    store = StateStore(tmp_path / "state.sqlite")
    clock = FakeClock(sleeps=6)
    poller = Poller(store, clock, user_agent("9.9"), transport=httpx.MockTransport(nws))
    real_once = poller.once

    async def once(url: str, accept: str = "*/*") -> bytes:
        if "/stations/KNOP" in url:
            raise TimeoutError
        return await real_once(url, accept)

    poller.once = once  # type: ignore[method-assign]
    emitted: list[Item] = []

    async def emit(item: Item) -> None:
        emitted.append(item)

    source = NwsWeather()
    ctx = PluginContext(source.spec, clock, {}, emit, 30.0, services={
        "poller": lambda: poller, "climate_codes": lambda: ["CLINOP", "CLIXYZ"],
    })  # fmt: skip

    async def go() -> None:
        try:
            await source.run(ctx)
        except (asyncio.CancelledError, ExceptionGroup):
            pass
        finally:
            await poller.aclose()

    asyncio.run(go())
    health = poller.health()
    notes = health["notes"]
    assert isinstance(notes, dict)
    assert notes["nws"]["unmapped"] == {"CLINOP": "TimeoutError"}
    assert notes["nws"]["mapped"] == ["CLIXYZ"] and emitted  # Made-up Town still recorded
    store.close()
