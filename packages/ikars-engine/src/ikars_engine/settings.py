"""The engine's settings: one versioned TOML file (PLAN D20 G, D19 G15).

Settings never hold a secret: only the *names* of the environment variables that do (PLAN D8). A
file written by an older version is migrated step by step, and the old file is kept as a backup
before the new one replaces it, so an upgrade can always be undone. Writes go to a temporary file
first and are then renamed over the old one, so a crash never leaves half a file.
"""

import re
import shutil
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import msgspec
import tomli_w
from ikars_core.fixedpoint import PrecisionError, to_e6

from ikars_engine.files import backup_name, write_text_atomic

VERSION = 5

# the terminal's built-in skins (PLAN G16 as amended): one list, here, for the settings, the API's
# contracts and (generated from them) the terminal
Skin = Literal["system", "dark", "light", "contrast", "midnight", "paper"]


class KalshiSettings(msgspec.Struct, frozen=True, kw_only=True):
    # production by design: IKARS runs on its own read-only production key (PLAN D21); the core
    # library's demo default is for code that does not say which (M1.R 27)
    environment: str = "production"
    key_id_variable: str = "IKARS_KALSHI_KEY_ID"
    key_path_variable: str = "IKARS_KALSHI_PRIVATE_KEY_PATH"


class LogSettings(msgspec.Struct, frozen=True, kw_only=True):
    level: str = "INFO"
    max_bytes: int = 10_000_000
    files_kept: int = 10


class BudgetSettings(msgspec.Struct, frozen=True, kw_only=True):
    monthly_e6: int = 40_000_000  # dollars in millionths, like all money
    degrade_at_percent: int = 80


class FeedSettings(msgspec.Struct, frozen=True, kw_only=True):
    watch: list[str] = []  # markets always in the watched set
    watched_max_markets: int = 100  # owner, 2026-10-27
    watched_max_deltas_per_second: int = 3_000
    read_share_percent: int = 25  # of the account's read bucket (owner, 2026-10-27)
    storage_budget_gb: int = 200  # for raw recordings (owner, 2026-10-27)


class BackupSettings(msgspec.Struct, frozen=True, kw_only=True):
    # the NAS share IKARS backs up to (its own ikars\ subtree only); empty: no backups. The real
    # path lives only in the local settings file, never in the repository (PLAN D18)
    share: str = ""


class AlertSettings(msgspec.Struct, frozen=True, kw_only=True):
    # workstation notifications only, no phone pushes (owner, 2026-10-27); in quiet hours only
    # urgent alerts pop up, the rest wait for the digest (owner: 22:00 to 07:00, workstation time)
    quiet_start: str = "22:00"
    quiet_end: str = "07:00"
    throttle_minutes: int = 10
    digest_minutes: int = 60


class LayoutWindow(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    # one IKARS window: its screen (an address in the terminal, like /markets?q=fed) and where it
    # sits, in Windows' screen coordinates, so any monitor (G16)
    screen: str = "/overview"
    x: int = 0
    y: int = 0
    width: int = 1280
    height: int = 800


class Layout(msgspec.Struct, frozen=True, kw_only=True, forbid_unknown_fields=True):
    name: str
    windows: list[LayoutWindow] = []


class TerminalSettings(msgspec.Struct, frozen=True, kw_only=True):
    # the loopback port the engine serves the web terminal and API on: fixed, so an open terminal
    # reconnects after a restart (review 1.R2 item 1); when it is taken, a free one and an alert
    port: int = 47821
    session_days: int = 30  # a terminal signs in again after this many days unused (owner)
    skin: Skin = (
        "system"  # the terminal's colours: system (follow Windows) or a built-in skin (G16)
    )
    layouts: list[Layout] = []  # saved sets of windows (G16; M1.8b-2)
    start_layout: str = ""  # the one ikars open restores; empty: one window on the Overview


class PluginDefaults(msgspec.Struct, frozen=True, kw_only=True):
    queue_size: int = 10_000
    call_timeout_seconds: float = 30.0
    restart_base_seconds: float = 1.0
    restart_max_seconds: float = 300.0


class Settings(msgspec.Struct, frozen=True, kw_only=True):
    version: int = VERSION
    tax_time_zone: str = ""  # an IANA name; empty: the workstation's zone (owner, 2026-10-27)
    kalshi: KalshiSettings = KalshiSettings()
    logs: LogSettings = LogSettings()
    budget: BudgetSettings = BudgetSettings()
    feed: FeedSettings = FeedSettings()
    plugins: PluginDefaults = PluginDefaults()
    backup: BackupSettings = BackupSettings()
    alerts: AlertSettings = AlertSettings()
    terminal: TerminalSettings = TerminalSettings()
    plugin_settings: dict[str, dict[str, Any]] = {}


class SettingsError(ValueError):
    """The settings file cannot be read, or holds a value it must not."""


# Words that make a key name a secret (whole words of the name, split on _ - .), and pairs that do.
SECRET_WORDS = frozenset(
    {
        "secret",
        "secrets",
        "password",
        "passwd",
        "token",
        "apikey",
        "credential",
        "credentials",
        "auth",
        "cookie",
        "bearer",
        "passphrase",
    }
)
SECRET_PAIRS = frozenset({("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key")})
# A key naming an environment variable holds a name, not a secret (PLAN D8)
VARIABLE_SUFFIX = "_variable"
_PEM_MARK = "-----BEGIN"


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    if lowered.endswith(VARIABLE_SUFFIX):
        return False
    words = [w for w in re.split(r"[_\-.\s]+", lowered) if w]
    return (
        lowered == "key"
        or any(w in SECRET_WORDS for w in words)
        or any(pair in SECRET_PAIRS for pair in zip(words, words[1:], strict=False))
    )


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    # version 1 kept the monthly budget in dollars as budget.monthly_dollars; converted exactly
    budget = dict(data.get("budget", {}))
    if "monthly_dollars" in budget:
        value = budget.pop("monthly_dollars")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise SettingsError(f"budget.monthly_dollars is not an amount: {value!r}")
        try:
            budget["monthly_e6"] = to_e6(str(value))
        except (ValueError, PrecisionError) as exc:
            raise SettingsError(f"budget.monthly_dollars is not an amount: {value!r}") from exc
    return {**data, "budget": budget, "version": 2}


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    # version 2's tax zone defaulted to America/New_York before the owner chose "the workstation's
    # zone"; a file still holding that default follows the workstation, any other choice is kept
    zone = data.get("tax_time_zone")
    return {**data, "tax_time_zone": "" if zone == OLD_DEFAULT_ZONE else zone or "", "version": 3}


def _v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    # version 4 adds [terminal] (the web terminal's port and session length) with its defaults;
    # rewriting the file shows the new settings to the owner
    return {**data, "version": 4}


def _v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    # version 5 adds the terminal's saved layouts (none yet) and the one opened at start
    return {**data, "version": 5}


OLD_DEFAULT_ZONE = "America/New_York"
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5,
}  # fmt: skip


def _version(data: dict[str, Any]) -> int:
    version = data.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SettingsError(f"the settings version must be a whole number from 1, got {version!r}")
    return version


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    version = _version(data)
    if version > VERSION:
        raise SettingsError(f"the settings are from a newer IKARS (version {version} > {VERSION})")
    while version < VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise SettingsError(f"no migration from settings version {version}")
        data = step(data)
        version = _version(data)
    return data


def _refuse_secrets(data: Any, where: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if _looks_secret(str(key)):
                raise SettingsError(
                    f"settings must not hold secrets ({where}{key}); name an environment variable"
                )
            _refuse_secrets(value, f"{where}{key}.")
    elif isinstance(data, list):
        for value in data:
            _refuse_secrets(value, where)
    elif isinstance(data, str) and _PEM_MARK in data:
        raise SettingsError(f"settings must not hold a key or certificate ({where.rstrip('.')})")


def load(path: Path) -> Settings:
    """The settings in ``path`` (defaults when it does not exist), migrated to this version. A
    migrated file is rewritten, with the old one kept beside it as a backup."""
    if not path.exists():
        return Settings()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SettingsError(f"the settings file is not valid TOML: {exc}") from exc
    _refuse_secrets(raw)
    migrated = migrate(dict(raw))  # raises SettingsError, never a bare error
    try:
        settings = msgspec.convert(migrated, Settings)
    except msgspec.ValidationError as exc:
        raise SettingsError(f"the settings file has a bad value: {exc}") from exc
    if _version(raw) != VERSION:
        backup(path)
        save(settings, path)
    return settings


def backup(path: Path) -> Path:
    """Copy ``path`` beside itself under a name that never overwrites an earlier backup."""
    target = backup_name(path)
    shutil.copy2(path, target)
    return target


def save(settings: Settings, path: Path) -> None:
    write_text_atomic(path, tomli_w.dumps(msgspec.to_builtins(settings)))
