"""M1.8a-web-1a: ``ikars open``, the watchdog's port rule and the made-up-data API server."""

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from ikars_engine import open_terminal as ot
from ikars_engine.demo_api import Market, serve
from ikars_engine.paths import Paths
from ikars_engine.watchdog import RULES, Observation, evaluate

# --- ikars open ---------------------------------------------------------------------------------


def write_api(paths: Paths, port: int = 47821) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    (paths.data / "api.json").write_text(json.dumps({"port": port, "token": "t"}), "utf-8")


def opener(paths: Paths, **kw: Any) -> tuple[dict[str, object], list[list[str]], list[list[str]]]:
    started: list[list[str]] = []
    launched: list[list[str]] = []
    result = ot.open_terminal(
        paths,
        start=started.append,
        run=launched.append,
        edge=lambda: Path("C:/Edge/msedge.exe"),
        sleep=lambda s: None,
        **kw,
    )
    return result, started, launched


def test_open_starts_the_engine_when_needed_and_opens_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(tmp_path)
    monkeypatch.setattr("ikars_engine.runmode.engine_running", lambda folder: False)
    asked: list[tuple[int, str]] = []

    def code(port: int, token: str) -> str:
        asked.append((port, token))
        return f"http://127.0.0.1:{port}/#code=c"

    write_api(paths)
    result, started, launched = opener(paths, code=code)
    assert result == {"opened": True, "started_engine": True, "port": 47821}
    assert started and started[0][-1] == "serve" and asked == [(47821, "t")]
    command = launched[0]
    assert command[1] == "--app=http://127.0.0.1:47821/#code=c"
    assert f"--user-data-dir={tmp_path / 'browser'}" in command
    assert "--disable-extensions" in command


def test_open_uses_a_running_engine_and_waits_for_its_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = Paths(tmp_path)
    monkeypatch.setattr("ikars_engine.runmode.engine_running", lambda folder: True)
    calls = [0]

    def code(port: int, token: str) -> str:
        calls[0] += 1
        if calls[0] < 3:
            raise OSError("not answering yet")
        return "http://127.0.0.1:1/#code=c"

    write_api(paths)
    result, started, launched = opener(paths, code=code)
    assert started == [] and result["started_engine"] is False and calls[0] == 3 and launched


def test_open_gives_up_in_plain_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ikars_engine.runmode.engine_running", lambda folder: True)
    with pytest.raises(ot.OpenError, match="did not answer"):
        opener(Paths(tmp_path), wait_s=0.0)


def test_edge_is_found_in_its_usual_places(tmp_path: Path) -> None:
    with pytest.raises(ot.OpenError, match="not found"):
        ot.edge_path({"ProgramFiles": str(tmp_path)})
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.write_bytes(b"")
    assert (
        ot.edge_path({"ProgramFiles(x86)": str(tmp_path / "none"), "LOCALAPPDATA": str(tmp_path)})
        == edge
    )


# --- the watchdog -------------------------------------------------------------------------------


def test_the_watchdog_says_when_the_terminal_s_port_was_taken() -> None:
    now = 10**18
    snap: dict[str, Any] = {"written_ns": now, "port_fallback": True}
    names = [p.name for p in evaluate(Observation(True, False, snap, now), RULES)]
    assert "terminal-port" in names
    snap["port_fallback"] = False
    assert "terminal-port" not in [
        p.name for p in evaluate(Observation(True, False, snap, now), RULES)
    ]


# --- the made-up-data API server ----------------------------------------------------------------


def test_made_up_quotes_move_and_books_follow() -> None:
    market = Market(30)
    before = dict(market.quotes)
    for step in range(5):
        market.tick(step)
    assert market.quotes != before and len(market.books) == 20
    assert all(1 <= bid // 10_000 <= 97 for bid, _ in market.quotes.values() if bid is not None)
    assert all(book.ready for book in market.books.values())


async def test_the_demo_server_signs_in_and_streams(tmp_path: Path) -> None:
    lines: list[str] = []
    task = asyncio.create_task(
        serve(tmp_path, 10, 50.0, 0, 3.0, out=lambda text, flush: lines.append(text))
    )
    for _ in range(200):
        if lines:
            break
        await asyncio.sleep(0.01)
    started = json.loads(lines[0])
    base = f"http://127.0.0.1:{started['port']}"
    code = started["url"].split("#code=")[1]
    page = {"Origin": base, "Sec-Fetch-Site": "same-origin"}
    async with httpx.AsyncClient(base_url=base, timeout=10) as http:
        assert (await http.get("/")).status_code == 200  # the terminal's files (or placeholder)
        assert (
            await http.post("/v1/session", json={"code": code}, headers=page)
        ).status_code == 204
        markets = (await http.get("/v1/markets?q=made-up")).json()
        assert markets["total"] == 10
        health = (await http.get("/v1/health")).json()
        assert health["feed"]["connected"] is True and health["api_streams"] == 0
        quotes = 0
        async with http.stream("GET", "/v1/stream") as stream:
            async for line in stream.aiter_lines():
                if line.startswith("data:") and "stream_id" in line:
                    stream_id = json.loads(line[5:])["stream_id"]
                    tickers = [m["ticker"] for m in markets["items"]]
                    path = f"/v1/stream/{stream_id}/subscriptions"
                    sub = await http.put(path, json={"quotes": tickers}, headers=page)
                    assert sub.status_code == 200
                if line.startswith("event: quote"):
                    quotes += 1
                    if quotes >= 20:  # first states, then changes
                        break
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert quotes >= 20


def test_open_waits_past_a_malformed_api_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1.8a-web-1a merge review: a null port raised TypeError, a traceback for the owner."""
    paths = Paths(tmp_path)
    monkeypatch.setattr("ikars_engine.runmode.engine_running", lambda folder: True)
    paths.data.mkdir(parents=True)
    (paths.data / "api.json").write_text(json.dumps({"port": None, "token": "t"}), "utf-8")
    with pytest.raises(ot.OpenError):
        opener(paths, wait_s=0.0)


def test_demo_api_refuses_a_rate_of_zero(capsys: pytest.CaptureFixture[str]) -> None:
    from ikars_engine.__main__ import main

    assert main(["ikars", "demo-api", "--hz", "0"]) == 2
    assert "must be above 0" in capsys.readouterr().err


@pytest.mark.parametrize("bid", [10_000, 970_000, 500_000])
def test_made_up_books_stay_valid_at_the_price_limits(bid: int) -> None:
    """Found by the M1.8a-web-1b screen tests: a level below 1 cent made the snapshot unreadable."""
    from ikars_engine.demo_api import made_up_book

    book = made_up_book("KXDEMO", bid, 1, 1)
    assert book.ready
    for side in ("yes", "no"):
        assert all(10_000 <= price <= 990_000 for price, _ in book.bids(side))
