"""Structured, rotated, redacted logs (PLAN D20 I).

Every record is one JSON line: time (UTC), level, logger, message, and any ``extra`` fields. A
redaction filter runs on every record before it is written, whoever logged it: the Kalshi key id
and key path (whatever the named environment variables hold), PEM blocks and bearer tokens become
``[redacted]`` in the message, the arguments and the extra fields. Account values are never passed
to a logger in the first place (a test enforces it); redaction is the second fence, not the first.
"""

import json
import logging
import logging.handlers
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REDACTED = "[redacted]"
_PEM = re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.S)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


def secret_forms(values: Iterable[str]) -> list[str]:
    """``values`` and how else each is written: a path with the other slash, and its file name
    alone (which names a key)."""
    found = [v for v in values if v]
    more = [v.replace("\\", "/") for v in found] + [v.replace("/", "\\") for v in found]
    names = [re.split(r"[\\/]", v)[-1] for v in found if "/" in v or "\\" in v]
    return found + more + names


class Redactor:
    """Masks secret values and secret-shaped text in strings."""

    def __init__(self, secrets: Iterable[str]) -> None:
        # longest first, so a value that contains another is masked whole; case-insensitive, since
        # Windows paths (and hex key ids) match in any case
        values = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(re.escape(v) for v in values), re.IGNORECASE) if values else None
        )

    @classmethod
    def from_environment(
        cls, variables: Iterable[str], environ: dict[str, str] | None = None
    ) -> "Redactor":
        env = dict(os.environ) if environ is None else environ
        return cls(secret_forms(env.get(name, "") for name in variables))

    def text(self, value: str) -> str:
        if self._pattern is not None:
            value = self._pattern.sub(REDACTED, value)
        value = _PEM.sub(REDACTED, value)
        return _BEARER.sub("Bearer " + REDACTED, value)

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(v) for v in value]
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self.text(str(value))


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._redactor.text(record.getMessage())
        record.args = None
        for key, value in list(vars(record).items()):
            if key not in _STANDARD:
                setattr(record, key, self._redactor.value(value))
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = self._redactor.text(
                logging.Formatter().formatException(record.exc_info)
            )
            record.exc_info = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD:
                entry[key] = value
        if record.exc_text:
            entry["exception"] = record.exc_text
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure(
    folder: Path,
    redactor: Redactor,
    *,
    level: str = "INFO",
    max_bytes: int = 10_000_000,
    files_kept: int = 10,
    logger: logging.Logger | None = None,
    filename: str = "ikars.jsonl",
) -> logging.Handler:
    """Send ``logger`` (the root logger by default) to ``folder/<filename>``, rotated by size
    (each process its own file: two processes rotating one file would clash on Windows).

    Redaction is a handler filter (a logger's own filters do not see records from child loggers),
    so it is put on **every** handler the logger has, not only this one. A handler added later must
    go through :func:`protect`."""
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        folder / filename, maxBytes=max_bytes, backupCount=files_kept, encoding="utf-8"
    )
    handler.setFormatter(JsonFormatter())
    target = logger or logging.getLogger()
    target.addHandler(handler)
    for each in target.handlers:
        protect(each, redactor)
    target.setLevel(level)
    # httpx logs every request at INFO: noise in a feed that makes thousands (KAPS did the same)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return handler


def protect(handler: logging.Handler, redactor: Redactor) -> logging.Handler:
    """Make ``handler`` redact every record before writing it (idempotent)."""
    if not any(isinstance(f, RedactingFilter) for f in handler.filters):
        handler.addFilter(RedactingFilter(redactor))
    return handler
