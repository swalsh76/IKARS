"""M1.8b-1: the settings the terminal shows and changes, and their actions (made-up folders)."""

import asyncio
from pathlib import Path

import httpx
import msgspec
import pytest
from ikars_engine import contracts as c
from ikars_engine.autostart import MemoryRegistry
from ikars_engine.paths import Paths
from ikars_engine.settings import Settings, load, save
from ikars_engine.settings_api import (
    RESTART,
    SettingsRefused,
    TerminalConfig,
    check_share,
    checked,
    key_status,
    restart_wanted,
)


def config(tmp_path: Path, *, saved: bool = True) -> TerminalConfig:
    paths = Paths(tmp_path).create()
    running = Settings()
    if saved:
        save(running, paths.settings)
    return TerminalConfig(paths, running, None, MemoryRegistry(), lambda argv: 0, restartable=True)


def test_the_view_shows_the_saved_settings_and_the_first_run(tmp_path: Path) -> None:
    fresh = config(tmp_path / "fresh", saved=False)
    assert fresh.view().first_run is True
    view = config(tmp_path / "set").view()
    assert view.first_run is False and view.skin == "system" and view.port == 47821
    assert view.restart_needed == [] and view.quiet_start == "22:00"


def test_a_change_is_checked_backed_up_and_saved(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    view = cfg.change(c.SettingsChange(skin="midnight", quiet_start=" 23:00 ", port=48000))
    assert view.skin == "midnight" and view.quiet_start == "23:00"
    assert view.restart_needed == ["port"]  # the running engine keeps its port until it restarts
    assert load(cfg.paths.settings).terminal.port == 48000
    assert len(list(cfg.paths.root.glob("settings.*.bak.toml"))) == 1  # the old file kept first


@pytest.mark.parametrize(
    ("change", "says"),
    [
        (c.SettingsChange(skin="neon"), "skin must be one of"),  # type: ignore[arg-type]
        (c.SettingsChange(port=80), "port must be a whole number from 1024"),
        (c.SettingsChange(session_days=0), "session_days"),
        (c.SettingsChange(quiet_start="late"), "quiet hours must be times"),
        (c.SettingsChange(tax_time_zone="Mars/Olympus"), "not a time zone"),
        (c.SettingsChange(watched_max_markets=True), "watched_max_markets"),
    ],
)
def test_a_change_out_of_range_is_refused_in_words(change: c.SettingsChange, says: str) -> None:
    with pytest.raises(SettingsRefused, match=says):
        checked(Settings(), change)


def test_a_first_save_makes_the_file_and_ends_the_first_run(tmp_path: Path) -> None:
    cfg = config(tmp_path, saved=False)
    assert cfg.change(c.SettingsChange(tax_time_zone="America/Denver")).first_run is False
    assert not list(cfg.paths.root.glob("*.bak.toml"))  # nothing to back up the first time


def test_the_key_status_is_yes_or_no_only(tmp_path: Path) -> None:
    key = tmp_path / "made-up.pem"
    key.write_text("not a real key", encoding="utf-8")
    env = {"IKARS_KALSHI_KEY_ID": "made-up-id", "IKARS_KALSHI_PRIVATE_KEY_PATH": str(key)}
    status = key_status(Settings(), env, {})  # never this machine's real variables
    assert status.id_set and status.path_set and status.file_found
    text = msgspec.json.encode(status).decode()
    assert "made-up-id" not in text and str(key) not in text and "made-up.pem" not in text
    missing = key_status(Settings(), {}, {})
    assert not missing.id_set and not missing.path_set and not missing.file_found
    assert missing.owner_only is None


def test_the_share_check_writes_only_inside_ikars_s_folder(tmp_path: Path) -> None:
    share = tmp_path / "share"
    assert check_share(str(share)).reachable is False
    share.mkdir()
    first = check_share(str(share))
    assert first.reachable and not first.marker and first.writable is None  # no IKARS folder yet
    assert list(share.iterdir()) == []  # nothing written outside it
    assert check_share("").problem is not None


async def test_setting_up_the_share_makes_ikars_s_folder_and_marker(tmp_path: Path) -> None:
    cfg = config(tmp_path / "engine")
    share = tmp_path / "share"
    share.mkdir()
    view = await cfg.share_init(str(share), 1_767_225_600 * 10**9)
    assert view.reachable and view.marker and view.writable and view.problem is None
    assert [p.name for p in share.iterdir()] == ["ikars"]
    assert (await cfg.share_check(str(share))).marker
    missing = await cfg.share_init(str(tmp_path / "nowhere"), 0)
    assert missing.reachable is False and missing.problem


def test_start_at_sign_in_is_installed_and_shown(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    done = cfg.install_autostart()
    assert done.done and "sign-in" in done.detail
    assert cfg.view().autostart_installed is True


def test_a_restart_is_asked_for_and_spent_once(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    assert restart_wanted(cfg.paths.data) is False
    assert cfg.restart().done
    assert (cfg.paths.data / RESTART).exists() and (cfg.paths.data / "stop.request").exists()
    assert not (cfg.paths.data / "stopped-by-owner").exists()  # not a stop by the owner
    assert restart_wanted(cfg.paths.data) is True and restart_wanted(cfg.paths.data) is False


# --- the routes ---------------------------------------------------------------------------------


async def test_the_settings_routes(tmp_path: Path) -> None:
    from zoneinfo import ZoneInfo

    from ikars_engine.api import ApiContext, create_app
    from ikars_engine.kalshi.discovery import MarketIndex
    from ikars_engine.ledger import Ledger
    from ikars_engine.metrics import Metrics
    from ikars_engine.portfolio import Held, portfolio

    cfg = config(tmp_path / "engine")
    ledger = Ledger(tmp_path / "ledger.sqlite", ZoneInfo("America/Denver"))
    ctx = ApiContext(
        token="t" * 43, health=dict, index=MarketIndex(), quotes={}, last_price={}, changed_ns={},
        book_changed_ns={}, books={}, book_version={}, watched=set, ledger=ledger,
        ledger_health=dict,
        portfolio=lambda: portfolio({}, lambda s: None, lambda s: None, Held({}, 0)),
        alert_log=tmp_path / "alerts.jsonl", metrics=Metrics(), now_ns=lambda: 0, port=47821,
        config=cfg,
    )  # fmt: skip
    auth = {"Authorization": f"Bearer {ctx.token}"}
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        view = msgspec.json.decode(
            (await http.get("/v1/settings", headers=auth)).content, type=c.SettingsView
        )
        assert view.skin == "system"
        changed = await http.put("/v1/settings", headers=auth, json={"skin": "paper"})
        assert changed.status_code == 200 and changed.json()["skin"] == "paper"
        refused = await http.put("/v1/settings", headers=auth, json={"skin": "neon"})
        assert refused.status_code == 400 and "neon" in refused.json()["detail"]  # not a skin
        unknown = await http.put("/v1/settings", headers=auth, json={"api_token": "x"})
        assert unknown.status_code == 400  # unknown fields refused
        share = tmp_path / "share"
        share.mkdir()
        checked_share = await http.post(
            "/v1/settings/share-check", headers=auth, json={"share": str(share)}
        )
        assert checked_share.json()["reachable"] is True
        made = await http.post("/v1/settings/share-init", headers=auth, json={"share": str(share)})
        assert made.json()["marker"] is True
        assert (await http.post("/v1/engine/autostart", headers=auth)).json()["done"] is True
        assert (await http.post("/v1/engine/restart", headers=auth)).json()["done"] is True
        assert (
            await http.post("/v1/settings/share-check", headers=auth, content=b"x")
        ).status_code == 400
    ctx.config = None
    transport = httpx.ASGITransport(app=create_app(ctx))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as http:
        assert (await http.get("/v1/settings", headers=auth)).status_code == 404
    ledger.close()


async def test_a_slow_share_does_not_stall_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    cfg = config(tmp_path)
    monkeypatch.setattr("ikars_engine.settings_api.SHARE_WAIT_S", 0.05)

    def slow(share: str) -> None:
        time.sleep(1)

    monkeypatch.setattr("ikars_engine.settings_api.check_share", slow)
    began = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await cfg.share_check("slow")
    assert asyncio.get_running_loop().time() - began < 0.5


def test_a_saved_share_reads_back_and_the_file_stays_loadable(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    cfg.change(c.SettingsChange(backup_share=r"\\made-up\share"))
    assert load(cfg.paths.settings).backup.share == r"\\made-up\share"


def test_an_unreadable_settings_file_is_refused_in_words_and_kept(tmp_path: Path) -> None:
    """Per-commit review: the view crashed on a settings file that could not be read."""
    cfg = config(tmp_path)
    cfg.paths.settings.write_text("version = [", encoding="utf-8")
    with pytest.raises(SettingsRefused, match="cannot be read"):
        cfg.view()
    with pytest.raises(SettingsRefused, match="cannot be read"):
        cfg.change(c.SettingsChange(skin="dark"))
    assert cfg.paths.settings.read_text(encoding="utf-8") == "version = ["  # never overwritten


# --- the M1.8b-1 merge review -------------------------------------------------------------------


def test_a_share_must_be_a_full_path_and_a_blank_zone_means_the_workstation_s() -> None:
    with pytest.raises(SettingsRefused, match="full path"):
        checked(Settings(), c.SettingsChange(backup_share="Documents"))
    assert checked(Settings(), c.SettingsChange(tax_time_zone="   ")).tax_time_zone == ""


def test_a_skin_makes_no_backup_and_backups_are_kept_to_the_latest(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    cfg.change(c.SettingsChange(skin="paper"))
    assert not list(cfg.paths.root.glob("settings.*.bak.toml"))
    for minutes in range(1, 26):
        cfg.change(c.SettingsChange(throttle_minutes=minutes))
    assert len(list(cfg.paths.root.glob("settings.*.bak.toml"))) == 20


def test_a_bad_zone_in_the_file_is_refused_in_words(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    text = cfg.paths.settings.read_text(encoding="utf-8").replace(
        'tax_time_zone = ""', 'tax_time_zone = "Mountain"'
    )
    cfg.paths.settings.write_text(text, encoding="utf-8")
    with pytest.raises(SettingsRefused, match="tax time zone"):
        cfg.view()


def test_a_key_set_in_windows_since_the_start_needs_a_restart(tmp_path: Path) -> None:
    key = tmp_path / "made-up.pem"
    key.write_text("x", encoding="utf-8")
    now = {"IKARS_KALSHI_KEY_ID": "id", "IKARS_KALSHI_PRIVATE_KEY_PATH": str(key)}
    status = key_status(Settings(), {}, now)
    assert status.id_set and status.file_found and status.needs_restart
    assert key_status(Settings(), now, {}).needs_restart is False


def test_a_foreground_engine_refuses_a_restart_it_could_not_carry_out(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    cfg.restartable = False
    done = cfg.restart()
    assert not done.done and "foreground" in done.detail
    assert not (cfg.paths.data / RESTART).exists()


def test_a_start_spends_a_stale_restart_request(tmp_path: Path) -> None:
    from ikars_engine import runmode

    cfg = config(tmp_path)
    cfg.restart()
    runmode.clear_stop(cfg.paths.data)  # what serve does when it starts
    assert not (cfg.paths.data / RESTART).exists() and not restart_wanted(cfg.paths.data)


def test_changes_at_once_take_turns_and_all_land(tmp_path: Path) -> None:
    """CI on main: two changes at once collided on the settings file's temporary copy."""
    from concurrent.futures import ThreadPoolExecutor

    cfg = config(tmp_path)
    changes = [c.SettingsChange(throttle_minutes=n) for n in range(1, 41)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(cfg.change, changes))
        list(pool.map(lambda _: cfg.view(), range(40)))
    assert 1 <= load(cfg.paths.settings).alerts.throttle_minutes <= 40  # never a failed write
