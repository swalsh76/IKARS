"""The root conftest.py's opt-in switches, run against a tiny suite (docs/TESTING.md)."""

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

ROOT_CONFTEST = (Path(__file__).parents[2] / "conftest.py").read_text(encoding="utf-8")
SUITE = """
import pytest

@pytest.mark.integration
def test_live(): pass

@pytest.mark.localdata
def test_local(): pass

def test_plain(): pass
"""


def outcomes(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, **env: str
) -> dict[str, int]:
    for name in ("IKARS_LIVE", "IKARS_LOCAL_DATA", "KALSHI_INTEGRATION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    pytester.makeconftest(ROOT_CONFTEST)
    pytester.makeini("[pytest]\nmarkers =\n    integration: live\n    localdata: local\n")
    pytester.makepyfile(SUITE)
    return pytester.runpytest("-p", "no:cacheprovider", "-p", "no:asyncio").parseoutcomes()


def test_opt_in_kinds_are_skipped_by_default(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert outcomes(pytester, monkeypatch) == {"passed": 1, "skipped": 2}


@pytest.mark.parametrize("value", ["0", "false", "yes", ""])
def test_live_tests_need_exactly_1(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    assert outcomes(pytester, monkeypatch, IKARS_LIVE=value) == {"passed": 1, "skipped": 2}


def test_live_tests_run_with_1(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> None:
    assert outcomes(pytester, monkeypatch, IKARS_LIVE="1") == {"passed": 2, "skipped": 1}


def test_the_kaps_switch_does_not_turn_live_tests_on(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert outcomes(pytester, monkeypatch, KALSHI_INTEGRATION="1") == {"passed": 1, "skipped": 2}


def test_local_data_needs_an_existing_folder(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing = str(tmp_path / "no-such-folder")
    assert outcomes(pytester, monkeypatch, IKARS_LOCAL_DATA=missing) == {"passed": 1, "skipped": 2}
    assert outcomes(pytester, monkeypatch, IKARS_LOCAL_DATA=str(tmp_path)) == {
        "passed": 2,
        "skipped": 1,
    }


def test_a_folder_named_like_a_marker_does_not_skip_unmarked_tests(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makeconftest(ROOT_CONFTEST)
    monkeypatch.delenv("IKARS_LIVE", raising=False)
    (pytester.path / "integration").mkdir()
    (pytester.path / "integration" / "test_unit.py").write_text("def test_unit(): pass\n")
    assert pytester.runpytest("-p", "no:cacheprovider", "-p", "no:asyncio").parseoutcomes() == {
        "passed": 1
    }
