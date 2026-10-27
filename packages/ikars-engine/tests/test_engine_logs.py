"""Planted secrets never reach a log file (PLAN D20 I)."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from ikars_engine.logs import REDACTED, Redactor, configure

KEY_ID = "00000000-1111-2222-3333-444444444444"
KEY_PATH = r"C:\Users\someone\.ssh\IKARS-RO.pem"
PEM = "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA\n-----END PRIVATE KEY-----"
ENV = {"IKARS_KALSHI_KEY_ID": KEY_ID, "IKARS_KALSHI_PRIVATE_KEY_PATH": KEY_PATH}


@pytest.fixture
def logged(tmp_path: Path) -> Iterator[tuple[logging.Logger, Path]]:
    logger = logging.getLogger(f"test.{tmp_path.name}")
    logger.propagate = False
    redactor = Redactor.from_environment(ENV, ENV)
    handler = configure(tmp_path, redactor, logger=logger, level="DEBUG")
    yield logger, tmp_path / "ikars.jsonl"
    logger.removeHandler(handler)
    handler.close()


def lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_records_are_json_lines_with_extra_fields(logged: tuple[logging.Logger, Path]) -> None:
    logger, path = logged
    logger.info("hello %s", "world", extra={"plugin": "nws", "count": 3})
    entry = lines(path)[0]
    assert entry["message"] == "hello world" and entry["level"] == "INFO"
    assert (
        entry["plugin"] == "nws" and entry["count"] == 3 and str(entry["time"]).endswith("+00:00")
    )


def test_planted_secrets_never_reach_the_file(logged: tuple[logging.Logger, Path]) -> None:
    logger, path = logged
    logger.warning("key %s at %s", KEY_ID, KEY_PATH)
    logger.warning("forward slashes %s", KEY_PATH.replace("\\", "/"))
    logger.warning("a pem %s", PEM)
    logger.warning("auth header", extra={"headers": {"Authorization": "Bearer abc.def-123"}})
    logger.warning("nested", extra={"detail": [KEY_ID, {"path": KEY_PATH}]})
    try:
        raise ValueError(f"cannot read {KEY_PATH} for {KEY_ID}")
    except ValueError:
        logger.exception("failed")
    text = path.read_text(encoding="utf-8")
    for secret in (KEY_ID, KEY_PATH, KEY_PATH.replace("\\", "/"), "MC4CAQAw", "abc.def-123"):
        assert secret not in text and json.dumps(secret)[1:-1] not in text
    assert text.count(REDACTED) >= 7


def test_short_or_empty_values_are_not_treated_as_secrets() -> None:
    redactor = Redactor(["", "ab"])
    assert redactor.text("ab cd") == "ab cd"
