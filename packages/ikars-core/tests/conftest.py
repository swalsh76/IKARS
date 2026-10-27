import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # so tests can import the fake server

FIXTURES = Path(__file__).parent / "fixtures"


# The opt-in rule for live tests (marker ``integration``) is the root conftest.py's, for every
# package.


@pytest.fixture
def sample() -> Callable[[str], dict[str, Any]]:
    """A fixture file read the way the client reads a response: floats kept as text."""

    def load(name: str) -> dict[str, Any]:
        text = (FIXTURES / name).read_text(encoding="utf-8")
        data: dict[str, Any] = json.loads(text, parse_float=str)
        return data

    return load
