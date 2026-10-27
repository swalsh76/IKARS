"""The engine's settings: one versioned TOML file (PLAN D20 G, D19 G15).

Settings never hold a secret: only the *names* of the environment variables that do (PLAN D8). A
file written by an older version is migrated step by step, and the old file is kept as a backup
before the new one replaces it, so an upgrade can always be undone. Writes go to a temporary file
first and are then renamed over the old one, so a crash never leaves half a file.
"""

import os
import re
import shutil
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
import tomli_w
from ikars_core.fixedpoint import PrecisionError, to_e6

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


class FeedSettings(msgspec.Struct, frozen=True, kw_only=True):
    watch: list[str] = []  # markets always in the watched set
    watched_max_markets: int = 100  # owner, 2026-10-27
    watched_max_deltas_per_second: int = 3_000
    read_share_percent: int = 25  # of the account's read bucket (owner, 2026-10-27)
    storage_budget_gb: int = 200  # for raw recordings (owner, 2026-10-27)


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
    feed: FeedSettings = FeedSettings()
    plugins: PluginDefaults = PluginDefaults()
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


MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {1: _v1_to_v2}


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
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = path.with_name(f"{path.stem}.{stamp}.bak{path.suffix}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.stem}.{stamp}-{n}.bak{path.suffix}")
        n += 1
    shutil.copy2(path, target)
    return target


def save(settings: Settings, path: Path) -> None:
    data = msgspec.to_builtins(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        file.write(tomli_w.dumps(data))
        file.flush()
        os.fsync(file.fileno())  # on disk before the rename, so a power cut leaves old or new
    os.replace(temporary, path)
