"""Opt-in test kinds (docs/TESTING.md): live Kalshi tests and tests that need the workstation's
own recordings are skipped unless asked for, and never run in CI."""

import os

import pytest

OPT_IN = {
    "integration": ("KALSHI_INTEGRATION", "live test: set KALSHI_INTEGRATION=1 to run"),
    "localdata": (
        "IKARS_LOCAL_DATA",
        "needs local recordings: set IKARS_LOCAL_DATA to their folder to run",
    ),
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, (variable, reason) in OPT_IN.items():
        if os.environ.get(variable):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
