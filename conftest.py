"""Opt-in test kinds (docs/TESTING.md): live Kalshi tests and tests that need the workstation's
own recordings are skipped unless asked for, and never run in CI.

The switches are IKARS's own (never KAPS's or SPARQ's ``KALSHI_*`` variables, which may be set on
the same machine) and are on only for an exact value, so ``IKARS_LIVE=0`` or ``false`` keeps them
off.
"""

import os
from pathlib import Path

import pytest


def _live_requested() -> bool:
    return os.environ.get("IKARS_LIVE") == "1"


def _local_data_requested() -> bool:
    folder = os.environ.get("IKARS_LOCAL_DATA", "")
    return bool(folder) and Path(folder).is_dir()


OPT_IN = {
    "integration": (_live_requested, "live test: set IKARS_LIVE=1 to run"),
    "localdata": (
        _local_data_requested,
        "needs local recordings: set IKARS_LOCAL_DATA to their folder to run",
    ),
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, (requested, reason) in OPT_IN.items():
        if requested():
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            # a real marker only: item.keywords would also match folder, module and test names
            if item.get_closest_marker(marker) is not None:
                item.add_marker(skip)
