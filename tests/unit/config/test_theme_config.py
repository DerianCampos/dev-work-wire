import json

import pytest

from devworkwire.config.paths import Paths
from devworkwire.config.theme_config import ThemeName, load_theme, save_theme


def test_missing_preference_defaults_to_dark_without_creating_files():
    assert load_theme() == ThemeName.dark
    assert not (Paths.user_config_dir() / "theme.json").exists()


@pytest.mark.parametrize("theme", list(ThemeName))
def test_theme_preference_survives_reload(theme):
    save_theme(theme)

    assert load_theme() == theme
    assert json.loads((Paths.user_config_dir() / "theme.json").read_text()) == {
        "theme": theme.value
    }


@pytest.mark.parametrize(
    "contents",
    ["not json", "{}", "[]", "null", '{"theme":"unknown"}', '{"theme":null}'],
)
def test_invalid_preference_reports_how_to_repair_it(contents):
    (Paths.user_config_dir() / "theme.json").write_text(contents)

    with pytest.raises(ValueError, match="dwire config theme NAME"):
        load_theme()


def test_failed_atomic_save_keeps_the_previous_preference(monkeypatch):
    save_theme(ThemeName.light)

    def fail_replace(*_):
        raise OSError("read-only directory")

    monkeypatch.setattr("devworkwire.config.theme_config.os.replace", fail_replace)

    with pytest.raises(OSError, match="read-only"):
        save_theme(ThemeName.dracula)

    assert load_theme() == ThemeName.light
    assert sorted(path.name for path in Paths.user_config_dir().iterdir()) == [
        "theme.json"
    ]


def test_theme_save_does_not_change_jira_storage():
    database = Paths.user_config_dir() / "config.db"
    database.write_bytes(b"existing Jira config")

    save_theme(ThemeName.light)

    assert database.read_bytes() == b"existing Jira config"
