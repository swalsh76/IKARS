"""The engine's settings: one versioned TOML file (PLAN D20 G, D19 G15).

Settings never hold a secret: only the *names* of the environment variables that do (PLAN D8). A
file written by an older version is migrated step by step, and the old file is kept as a backup
before the new one replaces it, so an upgrade can always be undone. Writes go to a temporary file
first and are then renamed over the old one, so a crash never leaves half a file.
"""

import os
import shutil
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import tomli_w

VERSION = 2


class KalshiSettings(msgspec.Struct, frozen=True, kw_only=True):
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


class PluginDefaults(msgspec.Struct, frozen=True, kw_only=True):
    queue_size: int = 10_000
    call_timeout_seconds: float = 30.0
    restart_base_seconds: float = 1.0
    restart_max_seconds: float = 300.0


class Settings(msgspec.Struct, frozen=True, kw_only=True):
    version: int = VERSION
    tax_time_zone: str = "America/New_York"
    kalshi: KalshiSettings = KalshiSettings()
    logs: LogSettings = LogSettings()
    budget: BudgetSettings = BudgetSettings()
    plugins: PluginDefaults = PluginDefaults()
    plugin_settings: dict[str, dict[str, Any]] = {}


class SettingsError(ValueError):
    """The settings file cannot be read, or holds a value it must not."""


SECRET_LOOKING = ("secret", "password", "token", "private_key")


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    # version 1 kept the monthly budget in whole dollars as budget.monthly_dollars
    budget = dict(data.get("budget", {}))
    if "monthly_dollars" in budget:
        budget["monthly_e6"] = int(budget.pop("monthly_dollars")) * 1_000_000
    return {**data, "budget": budget, "version": 2}


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _v1_to_v2}


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    version = int(data.get("version", 1))
    if version > VERSION:
        raise SettingsError(f"the settings are from a newer IKARS (version {version} > {VERSION})")
    while version < VERSION:
        data = MIGRATIONS[version](data)
        version = int(data["version"])
    return data


def _refuse_secrets(data: Any, where: str = "") -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            if any(word in str(key).lower() for word in SECRET_LOOKING):
                raise SettingsError(
                    f"settings must not hold secrets ({where}{key}); name an environment variable"
                )
            _refuse_secrets(value, f"{where}{key}.")


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
    migrated = migrate(dict(raw))
    try:
        settings = msgspec.convert(migrated, Settings)
    except msgspec.ValidationError as exc:
        raise SettingsError(f"the settings file has a bad value: {exc}") from exc
    if int(raw.get("version", 1)) != VERSION:
        backup(path)
        save(settings, path)
    return settings


def backup(path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.stem}.{stamp}.bak{path.suffix}")
    shutil.copy2(path, target)
    return target


def save(settings: Settings, path: Path) -> None:
    data = msgspec.to_builtins(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(temporary, path)
