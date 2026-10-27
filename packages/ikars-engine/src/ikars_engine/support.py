"""The redacted support bundle (PLAN D20 I; M1.8b-2).

One zip in ``<data root>\\support\\`` for the owner to look at, or to hand on if they choose; IKARS
never sends it anywhere. It holds the logs (the newest, up to a size), the health report when the
engine runs, the versions, the settings without the share's path, and the gap log.

It never holds a key, a key's id or path, the API token, the share's path or an account amount.
Two fences:

1. every text is redacted as it goes in: those values (paths with either slash, the key file's
   name alone), PEM blocks, bearer tokens, and the account's amounts as dollars, cents or
   millionths; the user's profile folder and the computer's name become placeholders;
2. the finished zip is read back and searched for the same values before it is kept: anything
   found and it is deleted, and the answer says where (never what).
"""

import json
import platform
import re
import sqlite3
import sys
import zipfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

import msgspec
import tomli_w

from ikars_engine import contracts as c
from ikars_engine.logs import Redactor, secret_forms
from ikars_engine.paths import Paths
from ikars_engine.settings import Settings, SettingsError, load
from ikars_engine.settings_api import user_environment

FOLDER = "support"
BUNDLES_KEPT = 10
LOG_BYTES = 50_000_000  # the newest logs, up to this much (the zip is a tenth of it or less)
GAPS_KEPT = 1_000
AMOUNT_FLOOR_E6 = 10_000_000  # an amount under $10 is too short a text to search for safely
REDACTED = "[redacted]"
AMOUNT = "[amount]"
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER = re.compile(r"(?i)\bbearer\s+(?!\[redacted\])[A-Za-z0-9._~+/=-]{8,}")


class BundleRefused(RuntimeError):
    """The bundle was not kept (said in plain words)."""


def amount_texts(amounts_e6: Iterable[int]) -> list[str]:
    """How an amount may be written: dollars (with and without a thousands comma), cents and
    millionths; amounts under AMOUNT_FLOOR_E6 are left out."""
    texts: set[str] = set()
    for amount in amounts_e6:
        e6 = abs(amount)
        if e6 < AMOUNT_FLOOR_E6:
            continue
        dollars, rest = divmod(e6, 1_000_000)
        cents = rest // 10_000
        texts |= {f"{dollars}.{cents:02d}", f"{dollars:,}.{cents:02d}", str(e6 // 10_000), str(e6)}
    return sorted(texts, key=len, reverse=True)


class Cleaner:
    """Redacts the secrets and amounts in a text, and finds any left in one."""

    def __init__(self, secrets: Iterable[str], amounts: Iterable[str], places: Mapping[str, str]):
        self._secrets = Redactor(secrets)
        self._words = sorted({s.casefold() for s in secrets if s and len(s) >= 4}, key=len)
        # an amount is a number of its own: not part of a longer number (times, ids)
        found = "|".join(re.escape(a) for a in amounts)
        self._amounts = re.compile(rf"(?<![\d.,])(?:{found})(?![\d])") if found else None
        kept = {k.casefold(): v for k, v in places.items() if k and len(k) >= 4}
        self._place_word = kept
        alternatives = "|".join(re.escape(k) for k in sorted(kept, key=len, reverse=True))
        self._places = re.compile(alternatives, re.IGNORECASE) if kept else None

    def text(self, value: str) -> str:
        value = self._secrets.text(value)
        if self._amounts is not None:
            value = self._amounts.sub(AMOUNT, value)
        if self._places is not None:
            value = self._places.sub(
                lambda m: self._place_word.get(m.group(0).casefold(), "[place]"), value
            )
        return value

    def leaks(self, value: str) -> bool:
        folded = value.casefold()
        return (
            any(word in folded for word in self._words)
            or bool(self._amounts is not None and self._amounts.search(value))
            or bool(_PEM.search(value) or _BEARER.search(value))
        )


def secrets_for(settings: Settings, environ: Mapping[str, str], token: str | None) -> list[str]:
    """The values a bundle must never hold: the key's id and path, the share and the API token."""
    names = (settings.kalshi.key_id_variable, settings.kalshi.key_path_variable)
    values = [environ.get(n, "") for n in names] + [settings.backup.share, token or ""]
    return escaped(secret_forms(values))


def escaped(values: Iterable[str]) -> list[str]:
    """``values`` and each as JSON writes it (a path's backslashes doubled): the logs and the gap
    log are JSON lines."""
    found = list(values)
    return found + [json.dumps(v, ensure_ascii=False)[1:-1] for v in found]


def places_for(environ: Mapping[str, str]) -> dict[str, str]:
    """The user's folder and the computer's name, and the placeholder each becomes."""
    found = {
        environ.get("USERPROFILE", ""): "%USERPROFILE%",
        environ.get("COMPUTERNAME", ""): "%COMPUTERNAME%",
    }
    with_slashes = {k.replace("\\", "/"): v for k, v in found.items()}
    as_json = {json.dumps(k, ensure_ascii=False)[1:-1]: v for k, v in found.items()}
    every = {**found, **with_slashes, **as_json}
    return dict(sorted(every.items(), key=lambda kv: -len(kv[0])))


def ledger_cash_e6(ledger: Path) -> list[int]:
    """The cash the real ledger explains (read only), for when the engine is not running."""
    if not ledger.is_file():
        return []
    try:
        with sqlite3.connect(f"{ledger.as_uri()}?mode=ro", uri=True) as db:
            row = db.execute("SELECT SUM(cash_e6) FROM record WHERE stream = 'real'").fetchone()
    except sqlite3.Error:
        return []
    return [int(row[0])] if row and row[0] is not None else []


def versions() -> dict[str, object]:
    packages = {
        d.metadata["Name"]: d.version for d in metadata.distributions() if d.metadata["Name"]
    }
    return {
        "ikars": {n: v for n, v in sorted(packages.items()) if n.lower().startswith("ikars")},
        "python": sys.version,
        "system": platform.platform(),
        "packages": dict(sorted(packages.items(), key=lambda kv: kv[0].lower())),
    }


def settings_text(settings: Settings) -> str:
    """The settings as TOML, the share's path left out (only whether one is set)."""
    data = msgspec.to_builtins(settings)
    data["backup"]["share"] = "[set]" if settings.backup.share else ""
    return tomli_w.dumps(data)


def gaps_text(state: Path) -> str:
    """The newest gaps from the engine's state (read only), as JSON lines."""
    if not state.is_file():
        return ""
    try:
        with sqlite3.connect(f"{state.as_uri()}?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT id, channel, reason, start_ns, end_ns, recovery, status, detail FROM gap"
                " ORDER BY id DESC LIMIT ?",
                (GAPS_KEPT,),
            ).fetchall()
    except sqlite3.Error as exc:
        return json.dumps({"problem": f"the gap log could not be read: {exc}"}) + "\n"
    keys = ("id", "channel", "reason", "start_ns", "end_ns", "recovery", "status", "detail")
    return "".join(json.dumps(dict(zip(keys, row, strict=True))) + "\n" for row in rows)


def newest_logs(folder: Path, budget: int = LOG_BYTES) -> list[Path]:
    """The log files, newest first, up to ``budget`` bytes in all."""
    found = sorted(
        (p for p in folder.glob("*.jsonl*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    kept: list[Path] = []
    for path in found:
        size = path.stat().st_size
        if kept and size > budget:
            break
        kept.append(path)
        budget -= size
    return kept


def check(bundle: Path, cleaner: Cleaner) -> list[str]:
    """The members of ``bundle`` that still hold a secret or an amount (empty: none)."""
    found: list[str] = []
    with zipfile.ZipFile(bundle) as archive:
        for member in archive.namelist():
            text = archive.read(member).decode("utf-8", errors="replace")
            if cleaner.leaks(member) or cleaner.leaks(text):
                found.append(member)
    return found


def make(
    paths: Paths,
    running: Settings,
    environ: Mapping[str, str],
    *,
    token: str | None = None,
    health: bytes | None = None,
    amounts_e6: Iterable[int] = (),
    now: datetime | None = None,
    windows: Mapping[str, str] | None = None,
) -> c.Done:
    """Build, check and keep a bundle; raises BundleRefused when the check finds anything."""
    try:
        settings = load(paths.settings) if paths.settings.exists() else running
    except SettingsError:
        settings = running  # the bundle is most wanted when the file is broken: the running ones
    names = (settings.kalshi.key_id_variable, settings.kalshi.key_path_variable)
    cleaner = Cleaner(
        secrets_for(settings, environ, token)
        # and as Windows has them now (set since the engine started; tests give their own)
        + secrets_for(settings, user_environment(names) if windows is None else windows, None)
        + escaped(secret_forms([running.backup.share])),
        amount_texts([*amounts_e6, *ledger_cash_e6(paths.ledger / "ledger.sqlite")]),
        places_for(environ),
    )
    at = (now or datetime.now(UTC)).astimezone()
    folder = paths.root / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"ikars-support-{at:%Y%m%d-%H%M%S}.zip"
    partial = final.with_suffix(".partial")
    texts = {
        "README.txt": (
            f"IKARS support bundle, made {at.isoformat(timespec='seconds')}.\n"
            "Redacted: keys, key ids and paths, the API token, the share's path and account\n"
            "amounts; the user's folder and the computer's name are placeholders. IKARS never\n"
            "sends this file anywhere.\n"
        ),
        "versions.json": json.dumps(versions(), indent=2),
        "settings.toml": settings_text(settings),
        "gaps.jsonl": gaps_text(paths.data / "state.sqlite"),
    }
    if health is not None:
        texts["health.json"] = json.dumps(json.loads(health), indent=2)
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, text in texts.items():
                archive.writestr(name, cleaner.text(text))
            logs = newest_logs(paths.logs)
            for log in logs:
                with (
                    log.open(encoding="utf-8", errors="replace") as lines,
                    archive.open(f"logs/{cleaner.text(log.name)}", "w") as out,
                ):
                    for line in lines:
                        out.write(cleaner.text(line).encode("utf-8"))
        problems = check(partial, cleaner)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    if problems:
        partial.unlink(missing_ok=True)
        raise BundleRefused(f"not kept: {', '.join(problems)} still held a secret or an amount")
    partial.replace(final)
    for old in sorted(folder.glob("ikars-support-*.zip"))[:-BUNDLES_KEPT]:
        old.unlink(missing_ok=True)
    count = len(texts) + len(logs)
    return c.Done(
        done=True,
        detail=f"saved as {FOLDER}\\{final.name} ({count} files; checked for keys, tokens, "
        "paths and amounts; never sent)",
    )
