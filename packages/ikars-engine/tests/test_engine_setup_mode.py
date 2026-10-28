"""M1.9a: setup mode, for a machine whose Kalshi key is not set up (a fresh install)."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pytest
from ikars_engine import watchdog
from ikars_engine.kalshi import runner
from ikars_engine.paths import Paths
from ikars_engine.runmode import snapshot_of
from ikars_engine.settings import Settings, TerminalSettings

NAMES = ("IKARS_KALSHI_KEY_ID", "IKARS_KALSHI_PRIVATE_KEY_PATH")


def test_a_missing_key_is_said_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    assert "must both be set" in (runner.key_problem(Settings()) or "")
    monkeypatch.setenv(NAMES[0], "made-up-id")
    monkeypatch.setenv(NAMES[1], "C:/nowhere/made-up.pem")
    problem = runner.key_problem(Settings()) or ""
    assert "not a Kalshi key id" in problem and "made-up-id" not in problem


async def test_setup_mode_serves_the_terminal_and_nothing_from_kalshi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "TICK_S", 0.05)
    paths = Paths(tmp_path).create()
    settings = Settings(terminal=TerminalSettings(port=0))  # any free port
    seen: list[dict[str, object]] = []
    stop = [False]
    task = asyncio.create_task(
        runner.run_setup(paths, settings, "the key is not set", lambda: stop[0], seen.append)
    )
    api = paths.data / "api.json"
    for _ in range(200):
        if api.exists():
            break
        await asyncio.sleep(0.05)
    record = json.loads(api.read_text(encoding="utf-8"))
    auth = {"Authorization": f"Bearer {record['token']}"}
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{record['port']}") as http:
        assert (await http.get("/v1/hello", headers=auth)).status_code == 200
        health = (await http.get("/v1/health", headers=auth)).json()
        assert health["feed"]["connected"] is False
        assert (await http.get("/v1/portfolio", headers=auth)).status_code == 200
        assert (await http.get("/v1/layouts", headers=auth)).status_code == 200
    stop[0] = True
    result = await asyncio.wait_for(task, 5)
    assert result["setup"] == {"reason": "the key is not set",
                               "next": "set the key, then restart the engine"}  # fmt: skip
    assert seen and snapshot_of(seen[0], 1)["setup"] == "the key is not set"


async def test_the_engine_goes_to_setup_mode_without_a_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "key_problem", lambda settings: "the key is not set")
    called: list[str] = []

    async def setup(paths: Paths, settings: Settings, problem: str, *args: Any) -> dict[str, Any]:
        called.append(problem)
        return {"setup": {"reason": problem}}

    async def never(*args: Any, **kw: Any) -> None:
        raise AssertionError("the full engine must not be built without a key")

    monkeypatch.setattr(runner, "run_setup", setup)
    monkeypatch.setattr(runner, "build", never)
    result = await runner.run(Paths(tmp_path), Settings(), None, should_stop=lambda: True)
    assert called == ["the key is not set"] and result["setup"] == {"reason": "the key is not set"}


def test_the_watchdog_tells_the_owner_and_does_not_restart_setup_mode() -> None:
    now = 10**18
    snap = {"written_ns": now, "setup": "the key is not set"}
    problems = watchdog.evaluate(watchdog.Observation(True, False, snap, now), watchdog.RULES)
    assert [p.name for p in problems] == ["setup"] and not problems[0].urgent


def test_a_fresh_engine_gets_the_key_windows_has_now() -> None:
    env = watchdog.engine_environment({"PATH": "x"}, {NAMES[0]: "made-up-id", NAMES[1]: ""})
    assert env == {"PATH": "x", NAMES[0]: "made-up-id"}  # an empty one is no value
    assert msgspec.json.encode(env)  # plain strings only


def test_the_key_is_read_from_the_variables_the_settings_name() -> None:
    from ikars_engine.settings import KalshiSettings

    settings = Settings(kalshi=KalshiSettings(key_id_variable="MY_ID", key_path_variable="MY_PATH"))
    env = runner.signer_environment(settings, {"MY_ID": "made-up-id", "IKARS_KALSHI_KEY_ID": "x"})
    assert env == {"IKARS_KALSHI_KEY_ID": "made-up-id", "IKARS_KALSHI_PRIVATE_KEY_PATH": ""}
    assert "must both be set" in (runner.key_problem(settings, {"MY_ID": "made-up-id"}) or "")


async def test_an_install_with_ledger_records_fails_loudly_instead_of_setup_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3
    from contextlib import closing

    from ikars_core.auth import AuthError

    paths = Paths(tmp_path).create()
    assert runner.has_history(paths.ledger / "ledger.sqlite") is False
    with closing(sqlite3.connect(paths.ledger / "ledger.sqlite")) as db, db:
        db.execute("CREATE TABLE record (stream TEXT)")
        assert runner.has_history(paths.ledger / "ledger.sqlite") is False  # empty: still fresh
        db.execute("INSERT INTO record VALUES ('real')")
    assert runner.has_history(paths.ledger / "ledger.sqlite") is True
    monkeypatch.setattr(runner, "key_problem", lambda settings: "the key file cannot be read")
    with pytest.raises(AuthError, match="cannot be read"):  # the watchdog restarts it, urgently
        await runner.run(paths, Settings(), None, should_stop=lambda: True)


async def test_setup_mode_ends_after_its_minutes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "TICK_S", 0.01)
    settings = Settings(terminal=TerminalSettings(port=0))
    result = await asyncio.wait_for(
        runner.run_setup(Paths(tmp_path).create(), settings, "no key", minutes=0.0005), 10
    )
    assert result["setup"]["reason"] == "no key"  # type: ignore[index]
