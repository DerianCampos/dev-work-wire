import json

import pytest
from typer.testing import CliRunner

from devworkwire.config.paths import Paths
from devworkwire.config.theme_config import ThemeName, load_theme, save_theme
from devworkwire.presentation.cli import main

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_color_environment(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)


def test_show_theme_does_not_require_jira_setup():
    result = runner.invoke(main.app, ["--format", "json", "config", "theme"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"] == {
        "saved": "dark",
        "effective": "dark",
        "available": ["dark", "light", "dracula", "solarized", "monochrome"],
    }


@pytest.mark.parametrize("theme", list(ThemeName))
def test_cli_saves_theme_and_uses_it_on_the_next_run(theme):
    saved = runner.invoke(main.app, ["config", "theme", theme.value])
    shown = runner.invoke(main.app, ["--format", "json", "config", "theme"])

    assert saved.exit_code == shown.exit_code == 0
    assert json.loads(shown.stdout)["data"]["effective"] == theme.value
    assert load_theme() == theme


def test_root_option_overrides_saved_theme_for_one_run():
    save_theme(ThemeName.light)

    result = runner.invoke(
        main.app, ["--format", "json", "--theme", "dracula", "config", "theme"]
    )
    next_run = runner.invoke(main.app, ["--format", "json", "config", "theme"])

    assert result.exit_code == next_run.exit_code == 0
    assert json.loads(result.stdout)["data"]["effective"] == "dracula"
    assert json.loads(next_run.stdout)["data"]["effective"] == "light"
    assert load_theme() == ThemeName.light


@pytest.mark.parametrize(
    "arguments",
    [["config", "theme", "invalid"], ["--theme", "invalid", "config", "theme"]],
)
def test_invalid_theme_returns_json_argument_error_without_changing_preference(
    arguments,
):
    save_theme(ThemeName.light)

    result = runner.invoke(main.app, ["--format", "json", *arguments])

    assert result.exit_code != 0
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_ARGUMENT"
    assert load_theme() == ThemeName.light


def test_no_color_overrides_runtime_theme_without_changing_saved_preference(
    monkeypatch,
):
    save_theme(ThemeName.dracula)
    monkeypatch.setenv("NO_COLOR", "1")

    result = runner.invoke(
        main.app, ["--format", "json", "--theme", "light", "config", "theme"]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["data"]["effective"] == "monochrome"
    assert load_theme() == ThemeName.dracula


def test_corrupt_settings_can_be_repaired_with_the_theme_command():
    (Paths.user_config_dir() / "theme.json").write_text("corrupt")

    result = runner.invoke(main.app, ["--format", "json", "config", "theme", "light"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["data"]["saved"] == "light"
    assert "Warning:" in result.stderr
    assert load_theme() == ThemeName.light


def test_theme_save_failure_reports_a_json_error(monkeypatch):
    def fail_save(_):
        raise OSError("permission denied")

    monkeypatch.setattr(main, "save_theme", fail_save)

    result = runner.invoke(main.app, ["--format", "json", "config", "theme", "light"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"]["message"].endswith("permission denied")


def test_jira_clear_preserves_the_theme():
    save_theme(ThemeName.light)

    result = runner.invoke(main.app, ["config", "clear", "--yes"])

    assert result.exit_code == 0
    assert load_theme() == ThemeName.light
