"""M1.8b-2: the terminal's layouts and the redacted support bundle (made-up folders and values)."""

import json
import sqlite3
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import httpx
import msgspec
import pytest
from ikars_engine import contracts as c
from ikars_engine import support
from ikars_engine.autostart import MemoryRegistry
from ikars_engine.paths import Paths
from ikars_engine.settings import VERSION, BackupSettings, Layout, LayoutWindow, Settings, load
from ikars_engine.settings import save as save_settings
from ikars_engine.settings_api import (
    LAYOUTS_KEPT,
    SettingsRefused,
    TerminalConfig,
    checked_layout,
)
from ikars_engine.store import StateStore


def config(tmp_path: Path) -> TerminalConfig:
    paths = Paths(tmp_path).create()
    save_settings(Settings(), paths.settings)
    return TerminalConfig(paths, Settings(), None, MemoryRegistry(), lambda argv: 0)


def window(screen: str = "/overview", **place: int) -> LayoutWindow:
    return LayoutWindow(screen=screen, **{"x": 0, "y": 0, "width": 1280, "height": 800, **place})


# --- layouts ------------------------------------------------------------------------------------


def test_a_layout_is_saved_replaced_by_name_chosen_for_the_start_and_deleted(
    tmp_path: Path,
) -> None:
    cfg = config(tmp_path)
    two = [window("/markets?q=fed", x=-1920, y=0, width=1600, height=1000), window()]
    view = cfg.save_layout(Layout(name=" Trading ", windows=two))
    assert [layout.name for layout in view.layouts] == ["Trading"] and view.start == "Trading"
    assert load(cfg.paths.settings).terminal.layouts[0].windows[0].x == -1920
    cfg.save_layout(Layout(name="Quiet", windows=[window()]))
    view = cfg.save_layout(Layout(name="trading", windows=[window("/ledger")]))  # same name
    assert [layout.name for layout in view.layouts] == ["trading", "Quiet"]
    assert view.layouts[0].windows == [window("/ledger")] and view.start == "trading"
    assert cfg.start_layout("QUIET").start == "Quiet"
    assert cfg.start_layout("").start == ""
    with pytest.raises(SettingsRefused, match="no layout is named"):
        cfg.start_layout("Nope")
    cfg.start_layout("Quiet")
    view = cfg.delete_layout("quiet")
    assert [layout.name for layout in view.layouts] == ["trading"] and view.start == ""
    with pytest.raises(SettingsRefused, match="no layout is named"):
        cfg.delete_layout("Quiet")
    assert len(list(cfg.paths.root.glob("settings.*.bak.toml"))) >= 1  # backed up first


def test_layouts_are_kept_to_a_limit(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    for n in range(LAYOUTS_KEPT):
        cfg.save_layout(Layout(name=f"L{n}", windows=[window()]))
    with pytest.raises(SettingsRefused, match="at most"):
        cfg.save_layout(Layout(name="one more", windows=[window()]))
    cfg.save_layout(Layout(name="L0", windows=[window("/alerts")]))  # replacing one still works


@pytest.mark.parametrize(
    ("layout", "says"),
    [
        (Layout(name="", windows=[window()]), "name"),
        (Layout(name="a/b", windows=[window()]), "name"),
        (Layout(name="..", windows=[window()]), "name"),
        (Layout(name="x" * 41, windows=[window()]), "name"),
        (Layout(name="none", windows=[]), "1 to 8 windows"),
        (Layout(name="many", windows=[window()] * 9), "1 to 8 windows"),
        (Layout(name="host", windows=[window("//evil.example/x")]), "not a screen"),
        (Layout(name="url", windows=[window("https://evil.example")]), "not a screen"),
        (Layout(name="space", windows=[window("/markets?q=a b")]), "not a screen"),
        (Layout(name="quote", windows=[window('/x" --flag')]), "not a screen"),
        (Layout(name="hash", windows=[window("/x#code=1")]), "not a screen"),
        (Layout(name="far", windows=[window(x=200_000)]), "too far"),
        (Layout(name="small", windows=[window(width=10)]), "width"),
        (Layout(name="tall", windows=[window(height=30_000)]), "height"),
    ],
)
def test_a_layout_that_could_leave_the_terminal_or_the_screens_is_refused(
    layout: Layout, says: str
) -> None:
    with pytest.raises(SettingsRefused, match=says):
        checked_layout(layout)


def test_version_4_settings_gain_no_layouts_and_are_kept_as_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("version = 4\n[terminal]\nskin = 'paper'\n", encoding="utf-8")
    settings = load(path)
    assert settings.version == VERSION  # migrated on, through every later version
    assert settings.terminal.layouts == [] and settings.terminal.start_layout == ""
    assert settings.terminal.skin == "paper"
    assert list(tmp_path.glob("settings.*.bak.toml"))


def test_a_window_with_an_unknown_field_is_refused() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"name":"a","windows":[{"screen":"/","frame":1}]}', type=Layout)
    with pytest.raises(msgspec.ValidationError):  # every field is required
        msgspec.json.decode(b'{"name":"a","windows":[{"screen":"/"}]}', type=Layout)


# --- the support bundle -------------------------------------------------------------------------

KEY_ID = "made-up-key-id-0123456789"
SHARE = r"\\made-up-host\made-up-share"
TOKEN = "made-up-token-abcdefghijklmnop"  # noqa: S105 (made up)
CASH_E6 = 1_234_560_000  # $1,234.56


def engine_folder(tmp_path: Path) -> tuple[Paths, dict[str, str]]:
    paths = Paths(tmp_path / "IKARS").create()
    key = tmp_path / "keys" / "made-up-key.pem"
    environ = {
        "IKARS_KALSHI_KEY_ID": KEY_ID,
        "IKARS_KALSHI_PRIVATE_KEY_PATH": str(key),
        "USERPROFILE": str(tmp_path),
        "COMPUTERNAME": "MADEUP-PC",
    }
    save_settings(Settings(backup=BackupSettings(share=SHARE)), paths.settings)
    lines = [
        f'{{"message": "signed with {KEY_ID}"}}',
        f'{{"message": "key at {key}; also {str(key).replace(chr(92), "/")}"}}',
        '{"message": "key file made-up-key.pem"}',
        f'{{"message": "share {SHARE} answered"}}',
        json.dumps({"message": f"as JSON writes them: {key} {SHARE} in {tmp_path}"}),
        f'{{"message": "Authorization: Bearer {TOKEN}"}}',
        f'{{"message": "token {TOKEN}"}}',
        '{"message": "balance 1,234.56 or 1234.56 or 123456 cents or 1234560000"}',
        '{"message": "at 91234.5 and 1712345600000000000 (not amounts)"}',
        f'{{"message": "on MADEUP-PC in {tmp_path}"}}',
        '{"message": "-----BEGIN RSA PRIVATE KEY-----\\nabc\\n-----END RSA PRIVATE KEY-----"}',
    ]
    (paths.logs / "ikars.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (paths.logs / "watchdog.jsonl").write_text('{"message": "checked"}\n', encoding="utf-8")
    state = StateStore(paths.data / "state.sqlite")
    state.add_gap("trade", "reconnect", 1, 2, "backfill", 3, f"lost on {SHARE}")
    state.close()
    return paths, environ


def bundle_text(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        return {n: archive.read(n).decode("utf-8") for n in archive.namelist()}


def test_a_bundle_holds_logs_health_versions_settings_and_gaps_and_no_secret(
    tmp_path: Path,
) -> None:
    paths, environ = engine_folder(tmp_path)
    done = support.make(paths, Settings(), environ, token=TOKEN, health=b'{"feed": {"ok": true}}',
                        amounts_e6=[CASH_E6, 5_000_000], windows={})  # fmt: skip
    assert done.done and "checked" in done.detail and SHARE not in done.detail
    [made] = list((paths.root / "support").glob("ikars-support-*.zip"))
    files = bundle_text(made)
    assert set(files) == {
        "README.txt", "versions.json", "settings.toml", "gaps.jsonl", "health.json",
        "logs/ikars.jsonl", "logs/watchdog.jsonl",
    }  # fmt: skip
    # the time holds an amount's digits inside a longer number: kept, and left out of this search
    everything = "\n".join(files.values()).replace("1712345600000000000", "")
    for secret in (KEY_ID, "made-up-key.pem", "made-up-host", "made-up-share", TOKEN, "1234.56",
                   "1,234.56", "123456 ", "1234560000", "MADEUP-PC", tmp_path.name,
                   "PRIVATE KEY"):  # fmt: skip
        assert secret.casefold() not in everything.casefold(), secret
    log = files["logs/ikars.jsonl"]
    assert "[amount]" in log and "[redacted]" in log and "%USERPROFILE%" in log
    assert "91234.5" in log and "1712345600000000000" in log  # numbers, not amounts
    assert 'share = "[set]"' in files["settings.toml"]
    assert '"channel": "trade"' in files["gaps.jsonl"]
    assert '"ok": true' in files["health.json"] and "ikars" in files["versions.json"]


def test_a_bundle_that_still_holds_a_secret_is_not_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, environ = engine_folder(tmp_path)
    monkeypatch.setattr(support.Cleaner, "text", lambda self, value: value)  # a broken fence
    with pytest.raises(support.BundleRefused, match="logs/ikars.jsonl") as refused:
        support.make(paths, Settings(), environ, token=TOKEN, windows={})
    assert KEY_ID not in str(refused.value)  # says where, never what
    assert list((paths.root / "support").iterdir()) == []  # nothing kept, not even a part


def test_bundles_are_kept_to_the_latest_ten(tmp_path: Path) -> None:
    paths, environ = engine_folder(tmp_path)
    for minute in range(12):
        at = datetime(2026, 10, 27, 12, minute, tzinfo=UTC)
        support.make(paths, Settings(), environ, now=at, windows={})
    assert len(list((paths.root / "support").glob("*.zip"))) == support.BUNDLES_KEPT


def test_without_the_engine_the_ledger_s_cash_is_still_redacted(tmp_path: Path) -> None:
    paths, environ = engine_folder(tmp_path)
    assert support.ledger_cash_e6(paths.ledger / "ledger.sqlite") == []
    with closing(sqlite3.connect(paths.ledger / "ledger.sqlite")) as db, db:
        db.execute("CREATE TABLE record (stream TEXT, cash_e6 INTEGER)")
        db.executemany("INSERT INTO record VALUES (?, ?)",
                       [("real", 1_000_000_000), ("real", 234_560_000), ("paper", 7)])  # fmt: skip
    assert support.ledger_cash_e6(paths.ledger / "ledger.sqlite") == [CASH_E6]
    support.make(paths, Settings(), environ, windows={})
    [made] = list((paths.root / "support").glob("*.zip"))
    assert "1234.56" not in bundle_text(made)["logs/ikars.jsonl"]


def test_amounts_are_written_every_way_and_small_ones_are_left_alone() -> None:
    assert support.amount_texts([-CASH_E6, 9_999_999]) == [
        "1234560000", "1,234.56", "1234.56", "123456",
    ]  # fmt: skip
    # $100.00 in cents is 10000, a common number: searched for as dollars and millionths only
    assert support.amount_texts([100_000_000]) == ["100000000", "100.00"]


def test_one_bundle_is_made_at_a_time(tmp_path: Path) -> None:
    paths, environ = engine_folder(tmp_path)
    with support._ONE_AT_A_TIME, pytest.raises(support.BundleRefused, match="already"):
        support.make(paths, Settings(), environ, windows={})
    assert support.make(paths, Settings(), environ, windows={}).done


def test_a_bundle_reads_old_settings_without_rewriting_them(tmp_path: Path) -> None:
    paths, environ = engine_folder(tmp_path)
    paths.settings.write_text("version = 4\n[terminal]\nskin = 'paper'\n", encoding="utf-8")
    support.make(paths, Settings(), environ, windows={})
    assert paths.settings.read_text(encoding="utf-8").startswith("version = 4")
    assert not list(paths.root.glob("settings.*.bak.toml"))


def test_a_broken_settings_file_still_gives_a_bundle(tmp_path: Path) -> None:
    paths, environ = engine_folder(tmp_path)
    paths.settings.write_text("version = [", encoding="utf-8")
    running = Settings(backup=BackupSettings(share=SHARE))
    assert support.make(paths, running, environ, windows={}).done


def test_the_command_makes_a_bundle_when_no_engine_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ikars_engine.runmode_cli import support_bundle

    paths, environ = engine_folder(tmp_path)
    monkeypatch.setattr("ikars_engine.runmode.engine_running", lambda folder: False)
    monkeypatch.setattr("ikars_engine.support.user_environment", lambda names: {})
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    # a stopped engine's token is still redacted: it outlives restarts (merge review 3)
    (paths.data / "api.json").write_text(json.dumps({"port": 1, "token": TOKEN}), "utf-8")
    with (paths.logs / "ikars.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps({"message": f"the token alone: {TOKEN}"}) + "\n")
    done = support_bundle(paths, Settings())
    [made] = list((paths.root / "support").glob("*.zip"))
    assert done.done and TOKEN not in "".join(bundle_text(made).values())


def test_a_half_written_api_file_falls_back_to_a_bundle_made_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ikars_engine.runmode_cli import support_bundle

    paths, environ = engine_folder(tmp_path)
    monkeypatch.setattr("ikars_engine.runmode.engine_running", lambda folder: True)
    monkeypatch.setattr("ikars_engine.support.user_environment", lambda names: {})
    (paths.data / "api.json").write_text(json.dumps({"token": TOKEN}), "utf-8")  # no port
    assert support_bundle(paths, Settings()).done


# --- the routes ---------------------------------------------------------------------------------


async def test_the_layout_and_support_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zoneinfo import ZoneInfo

    from ikars_engine.api import ApiContext, create_app
    from ikars_engine.kalshi.discovery import MarketIndex
    from ikars_engine.ledger import Ledger
    from ikars_engine.metrics import Metrics
    from ikars_engine.portfolio import Held, portfolio

    paths, environ = engine_folder(tmp_path)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("ikars_engine.support.user_environment", lambda names: {})
    cfg = TerminalConfig(paths, Settings(backup=BackupSettings(share=SHARE)), None,
                         MemoryRegistry(), lambda argv: 0)  # fmt: skip
    ledger = Ledger(tmp_path / "ledger.sqlite", ZoneInfo("America/Denver"))
    ctx = ApiContext(
        token=TOKEN, health=dict, index=MarketIndex(), quotes={}, last_price={}, changed_ns={},
        book_changed_ns={}, books={}, book_version={}, watched=set, ledger=ledger,
        ledger_health=dict,
        portfolio=lambda: portfolio({}, lambda s: None, lambda s: None, Held({}, CASH_E6)),
        alert_log=tmp_path / "alerts.jsonl", metrics=Metrics(), now_ns=lambda: 0, port=47821,
        config=cfg,
    )  # fmt: skip
    auth = {"Authorization": f"Bearer {TOKEN}"}
    place: dict[str, object] = {"screen": "/overview", "x": 0, "y": 0, "width": 1280, "height": 800}
    layout = {"name": "Trading", "windows": [place]}
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        assert (await http.get("/v1/layouts", headers=auth)).json() == {"layouts": [], "start": ""}
        saved = await http.put("/v1/layouts", headers=auth, json=layout)
        view = msgspec.json.decode(saved.content, type=c.LayoutsView)
        assert view.start == "Trading" and view.layouts[0].windows[0].width == 1280
        bad = {**layout, "windows": [{**place, "screen": "//evil.example"}]}
        assert (await http.put("/v1/layouts", headers=auth, json=bad)).status_code == 400
        sneaky = {**layout, "token": "x"}
        assert (await http.put("/v1/layouts", headers=auth, json=sneaky)).status_code == 400
        start = await http.put("/v1/layouts/start", headers=auth, json={"name": ""})
        assert start.json()["start"] == ""
        assert (await http.delete("/v1/layouts/Trading", headers=auth)).json()["layouts"] == []
        assert (await http.delete("/v1/layouts/Trading", headers=auth)).status_code == 400
        # a change from another page is refused by the guard, whatever the route
        cookie_less = await http.put("/v1/layouts", json=layout)
        assert cookie_less.status_code == 401
        made = await http.post("/v1/support/bundle", headers=auth)
        assert made.status_code == 200 and made.json()["done"] is True
    [bundle] = list((paths.root / "support").glob("*.zip"))
    files = bundle_text(bundle)
    assert "health.json" in files and TOKEN not in "".join(files.values())
    assert "1234.56" not in files["logs/ikars.jsonl"]  # the engine's cash, from its portfolio
    ledger.close()
