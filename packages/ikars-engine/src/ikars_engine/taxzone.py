"""The owner's tax time zone (PLAN D18): year boundaries and the local time on each ledger record.

The setting holds an IANA zone name. Empty (the default, owner 2026-10-27) means the workstation's
zone: Windows names it in the registry (``TimeZoneKeyName``), and CLDR's table maps the Windows name
to an IANA one. A zone that cannot be found falls back to UTC, and health says so, so that the
owner sets it explicitly; UTC on every record stays the authority either way.

Windows ships no IANA database, so ``zoneinfo`` reads the ``tzdata`` package.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# CLDR windowsZones.xml, the default (territory 001) zone of each Windows name a filer is likely in
WINDOWS_TO_IANA = {
    "Eastern Standard Time": "America/New_York",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "US Mountain Standard Time": "America/Phoenix",
    "Pacific Standard Time": "America/Los_Angeles",
    "Alaskan Standard Time": "America/Anchorage",
    "Aleutian Standard Time": "America/Adak",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "Atlantic Standard Time": "America/Halifax",
    "Newfoundland Standard Time": "America/St_Johns",
    "Canada Central Standard Time": "America/Regina",
    "Yukon Standard Time": "America/Whitehorse",
    "Central America Standard Time": "America/Guatemala",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Tokyo Standard Time": "Asia/Tokyo",
    "AUS Eastern Standard Time": "Australia/Sydney",
}


@dataclass(frozen=True, slots=True)
class TaxZone:
    zone: ZoneInfo
    source: str  # "setting", "workstation" or "fallback"

    @property
    def name(self) -> str:
        return self.zone.key


def windows_zone_name() -> str | None:
    """The workstation's Windows time zone name, or None off Windows or when it cannot be read."""
    if sys.platform != "win32":
        return None
    import winreg

    key = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as handle:
            value, _ = winreg.QueryValueEx(handle, "TimeZoneKeyName")
    except OSError:
        return None
    return str(value).strip("\x00 ") or None


def resolve(setting: str, windows_name: Callable[[], str | None] = windows_zone_name) -> TaxZone:
    """The zone the setting names; the workstation's when it is empty; UTC when neither is known.
    A setting that names no zone is an error: a wrong year boundary must never pass silently."""
    if setting:
        try:
            return TaxZone(ZoneInfo(setting), "setting")
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"tax_time_zone {setting!r} is not a time zone") from exc
    iana = WINDOWS_TO_IANA.get(windows_name() or "")
    if iana is not None:
        return TaxZone(ZoneInfo(iana), "workstation")
    return TaxZone(ZoneInfo("UTC"), "fallback")
