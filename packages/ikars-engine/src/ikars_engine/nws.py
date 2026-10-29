"""NWS weather (M2.2; PLAN §4.3 D22): observations, forecasts and the daily climate report for the
stations Kalshi's weather markets name, recorded from the first slice (D20 A).

**The stations come from the markets' rules** (owner, 2026-10-28: not a hand-made list): a weather
market's rules name the NWS climate report it reads ("recorded at New York City (CLINYC)"), and the
engine offers the codes of open markets as the service ``"climate_codes"``. A code's station is
``K`` and its last three letters (CLINYC is KNYC, Central Park), confirmed at ``/stations``; the
station's coordinates give its forecast grid at ``/points`` (rounded to four places, which NWS asks
for). A code that does not map is shown in health and never guessed; it is tried again each hour (a
lookup can fail for a while). Each hour, codes named by newly opened markets start their feeds and
codes whose markets have all closed stop theirs.

**The settlement source.** Kalshi settles these markets on The Weather Company's value, from the
same government station; The Weather Company offers no free feed and no terms for automated access,
so the NWS climate report is recorded as the proxy (owner, 2026-10-28). How close it is gets
measured against Kalshi's settlements in M2.4.

Per station, through the shared poller (M2.1):

- ``weather_obs``: the latest observation, about every 5 minutes (NWS's max-age is 280 s);
- ``weather_forecast``: the hourly and the daily forecast, each hourly (the first 48 hours kept);
- ``weather_climate``: the latest climate report, every 15 minutes, parsed (the day, whether it is
  the preliminary "today" or the final "yesterday", the high, the low, precipitation and snow,
  with "T" for a trace and None for "MM") and its text kept.

Values are kept as NWS gives them, with their units. Every request is a public GET; no key.
"""

import json
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ikars_engine.plugins import PluginContext, PluginKind, PluginSpec
from ikars_engine.polling import Feed, Found, Poller, PollError, PollingSource

API = "https://api.weather.gov"
GEO = "application/geo+json"
OBS_EVERY_S = 300.0
FORECAST_EVERY_S = 3_600.0
CLIMATE_EVERY_S = 900.0
CODES_EVERY_S = 3_600.0  # new cities named by new markets
HOURS_KEPT = 48
NS = 1_000_000_000


def station_of(code: str) -> str:
    """CLINYC -> KNYC: the climate report's location with the K of a US station."""
    return "K" + code.removeprefix("CLI")


def _ns(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * NS)
    except ValueError:
        return None


def _value(field: Any) -> dict[str, Any] | None:
    """NWS's quantity ({"value": ..., "unitCode": ...}) as given, or None when it has none."""
    if isinstance(field, dict) and field.get("value") is not None:
        return {"value": field["value"], "unit": field.get("unitCode")}
    return None


def parse_observation(station: str, body: bytes) -> list[Found]:
    props = json.loads(body).get("properties") or {}
    stamp = props.get("timestamp")
    if not stamp:
        return []
    payload = {
        "station": station,
        "timestamp": stamp,
        "text": props.get("textDescription"),
        **{
            key: _value(props.get(key))
            for key in (
                "temperature", "dewpoint", "windSpeed", "windDirection", "windGust",
                "barometricPressure", "relativeHumidity", "precipitationLastHour",
                "maxTemperatureLast24Hours", "minTemperatureLast24Hours",
            )
        },
    }  # fmt: skip
    return [Found(id=f"{station}@{stamp}", published_ns=_ns(stamp), payload=payload)]


def parse_forecast(station: str, which: str, body: bytes) -> list[Found]:
    props = json.loads(body).get("properties") or {}
    updated = props.get("updateTime") or props.get("generatedAt")
    if not updated:
        return []
    periods = [
        {
            "start": p.get("startTime"),
            "end": p.get("endTime"),
            "daytime": p.get("isDaytime"),
            "temperature": p.get("temperature"),
            "unit": p.get("temperatureUnit"),
            "rain_chance": _value(p.get("probabilityOfPrecipitation")),
            "wind": p.get("windSpeed"),
            "short": p.get("shortForecast"),
        }
        for p in (props.get("periods") or [])[: HOURS_KEPT if which == "hourly" else None]
    ]
    payload = {"station": station, "which": which, "updated": updated, "periods": periods}
    return [Found(id=f"{station}:{which}@{updated}", published_ns=_ns(updated), payload=payload)]


_SUMMARY = re.compile(r"CLIMATE SUMMARY FOR ([A-Z]+ \d{1,2} \d{4})")
_VALID = re.compile(r"VALID (?:TODAY )?AS OF (\d{3,4} [AP]M) LOCAL TIME")
_SECTION = re.compile(r"^\s*(TODAY|YESTERDAY)\s*$", re.MULTILINE)


def _row(text: str, label: str, after: int) -> str | None:
    """The first value on the row named ``label`` after position ``after`` ("MM": None)."""
    found = re.compile(rf"^\s*{label}\s+(\S+)", re.MULTILINE).search(text, after)
    if found is None or found.group(1) == "MM":
        return None
    return found.group(1)


def parse_climate(code: str, body: bytes) -> list[Found]:
    """The climate report: the day, today (preliminary) or yesterday (final), and its values."""
    product = json.loads(body)
    text = product.get("productText") or ""
    pid = product.get("id")
    if not pid or not text:
        return []
    summary = _SUMMARY.search(text)
    section = _SECTION.search(text)
    start = section.end() if section else 0
    precip = re.search(r"PRECIPITATION \(IN\)", text)
    snow = re.search(r"SNOWFALL \(IN\)", text)
    span = section.group(1).lower() if section else None
    high, low = _row(text, "MAXIMUM", start), _row(text, "MINIMUM", start)
    payload = {
        "code": code,
        "product": pid,
        "issued": product.get("issuanceTime"),
        "office": product.get("issuingOffice"),
        "day": summary.group(1) if summary else None,
        "span": span,  # "today": preliminary, "yesterday": final for that day
        "valid_as_of": (v.group(1) if (v := _VALID.search(text)) else None),
        "high_f": int(high) if high and high.lstrip("-").isdigit() else None,
        "low_f": int(low) if low and low.lstrip("-").isdigit() else None,
        "precip_in": _row(text, span.upper(), precip.end()) if precip and span else None,
        "snow_in": _row(text, span.upper(), snow.end()) if snow and span else None,
        "text": text,
    }
    return [Found(id=pid, published_ns=_ns(product.get("issuanceTime")), payload=payload)]


class NwsWeather(PollingSource):
    spec = PluginSpec(
        name="nws",
        kind=PluginKind.SOURCE,
        version="1",
        produces=("weather_obs", "weather_forecast", "weather_climate"),
        rate_per_second=1.0,
    )
    item_kind = "weather_obs"

    def parse(self, feed: Feed, body: bytes, ctx: PluginContext) -> list[Found]:
        _, key, which = feed.name.split(".")
        if which == "obs":
            return parse_observation(key, body)
        if which == "cli":
            return parse_climate(key, body)
        return parse_forecast(key, which, body)

    async def manage(
        self,
        ctx: PluginContext,
        poller: Poller,
        start: Callable[[Feed], None],
        stop: Callable[[str], None],
    ) -> None:
        if ctx.settings.get("enabled", True) is False:
            ctx.log.info("the nws source is turned off in the settings")
            return
        codes = ctx.services.get("climate_codes")
        if codes is None:
            ctx.log.warning("no climate codes in this engine: the nws source stays idle")
            return
        note = poller.notes.setdefault(self.spec.name, {"mapped": [], "unmapped": {}})
        running: dict[str, list[str]] = {}  # code -> its feeds' names
        while True:
            open_codes = set(codes())
            for code in sorted(open_codes - running.keys()):  # new, or not mapped yet
                try:
                    feeds = await self._feeds_for(code, poller, ctx)
                except (PollError, ValueError, KeyError, TypeError) as exc:
                    reason = str(exc) if isinstance(exc, PollError) else type(exc).__name__
                    note["unmapped"][code] = reason  # tried again next hour (per-commit review)
                    ctx.log.warning("a climate code did not map", extra={"code": code})
                    continue
                note["unmapped"].pop(code, None)
                running[code] = [feed.name for feed in feeds]
                for feed in feeds:
                    start(feed)
            for code in sorted(running.keys() - open_codes):  # its markets have all closed
                for name in running.pop(code):
                    stop(name)
            for code in [c for c in note["unmapped"] if c not in open_codes]:
                del note["unmapped"][code]
            note["mapped"] = sorted(running)
            await ctx.clock.sleep(CODES_EVERY_S)

    async def _feeds_for(self, code: str, poller: Poller, ctx: PluginContext) -> list[Feed]:
        """The station and forecast grid behind ``code``, as its four feeds."""
        station = station_of(code)
        about = json.loads(await ctx.call(poller.once(f"{API}/stations/{station}", GEO)))
        lon, lat = about["geometry"]["coordinates"][:2]
        point = json.loads(await ctx.call(poller.once(f"{API}/points/{lat:.4f},{lon:.4f}", GEO)))
        props = point["properties"]
        location = code.removeprefix("CLI")
        return [
            Feed(name=f"nws.{station}.obs", url=f"{API}/stations/{station}/observations/latest",
                 every_s=OBS_EVERY_S, accept=GEO, kind="weather_obs"),
            # named by station, not grid: two stations on one grid never share (or stop) a feed
            Feed(name=f"nws.{station}.hourly", url=props["forecastHourly"],
                 every_s=FORECAST_EVERY_S, accept=GEO, kind="weather_forecast"),
            Feed(name=f"nws.{station}.daily", url=props["forecast"],
                 every_s=FORECAST_EVERY_S, accept=GEO, kind="weather_forecast"),
            Feed(name=f"nws.{code}.cli",
                 url=f"{API}/products/types/CLI/locations/{location}/latest",
                 every_s=CLIMATE_EVERY_S, accept="application/ld+json", kind="weather_climate"),
        ]  # fmt: skip
