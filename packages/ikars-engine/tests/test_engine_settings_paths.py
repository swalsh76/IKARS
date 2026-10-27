from pathlib import Path

import pytest
from ikars_engine.paths import PORTABLE_MARKER, resolve
from ikars_engine.settings import VERSION, Settings, SettingsError, load, migrate, save


def test_installed_data_lives_under_local_app_data(tmp_path: Path) -> None:
    paths = resolve(tmp_path / "program", {"LOCALAPPDATA": str(tmp_path / "lad")})
    assert paths.root == tmp_path / "lad" / "IKARS"
    assert paths.settings == paths.root / "settings.toml"


def test_a_portable_marker_keeps_data_beside_the_program(tmp_path: Path) -> None:
    (tmp_path / PORTABLE_MARKER).write_text("")
    paths = resolve(tmp_path, {"LOCALAPPDATA": str(tmp_path / "lad")})
    assert paths.root == tmp_path / "data-portable"


def test_without_local_app_data_a_home_folder_is_used(tmp_path: Path) -> None:
    assert resolve(tmp_path, {}).root.name == "IKARS"


def test_create_makes_every_folder(tmp_path: Path) -> None:
    paths = resolve(tmp_path, {"LOCALAPPDATA": str(tmp_path)}).create()
    for folder in (paths.logs, paths.data, paths.recordings, paths.ledger, paths.backups):
        assert folder.is_dir()


def test_missing_settings_are_the_defaults(tmp_path: Path) -> None:
    assert load(tmp_path / "settings.toml") == Settings()


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    original = Settings(tax_time_zone="America/Denver", plugin_settings={"nws": {"poll_s": 60}})
    save(original, path)
    assert load(path) == original
    assert not list(tmp_path.glob("*.tmp"))


def test_an_old_file_is_migrated_and_backed_up(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("version = 1\n[budget]\nmonthly_dollars = 25\n", encoding="utf-8")
    settings = load(path)
    assert settings.budget.monthly_e6 == 25_000_000 and settings.version == VERSION
    backups = list(tmp_path.glob("settings.*.bak.toml"))
    assert len(backups) == 1 and "monthly_dollars" in backups[0].read_text(encoding="utf-8")
    assert "monthly_e6" in path.read_text(encoding="utf-8")


def test_a_newer_file_is_refused() -> None:
    with pytest.raises(SettingsError, match="newer IKARS"):
        migrate({"version": VERSION + 1})


@pytest.mark.parametrize(
    "text",
    [
        "api_token = 'x'\n",
        "[kalshi]\nprivate_key = 'x'\n",
        "[plugin_settings.odds]\npassword = 'x'\n",
    ],
)
def test_settings_never_hold_secrets(tmp_path: Path, text: str) -> None:
    path = tmp_path / "settings.toml"
    path.write_text(f"version = {VERSION}\n" + text, encoding="utf-8")
    with pytest.raises(SettingsError, match="must not hold secrets"):
        load(path)


def test_invalid_toml_and_bad_values_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "settings.toml"
    path.write_text("version = [", encoding="utf-8")
    with pytest.raises(SettingsError, match="not valid TOML"):
        load(path)
    path.write_text(f"version = {VERSION}\n[budget]\nmonthly_e6 = 'lots'\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="bad value"):
        load(path)
