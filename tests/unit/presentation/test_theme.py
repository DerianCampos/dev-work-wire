import pytest
import typer
from prompt_toolkit.styles import Style
from typer.testing import CliRunner

from devworkwire.config.theme_config import ThemeName
from devworkwire.presentation.cli import main, menu_view
from devworkwire.presentation.cli.theme import (
    PALETTES,
    active_theme,
    current_palette,
    prompt_style,
    themed_echo,
)


@pytest.fixture(autouse=True)
def reset_theme(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    token = active_theme.set(ThemeName.dark)
    yield
    active_theme.reset(token)


@pytest.mark.parametrize("name", list(ThemeName))
def test_each_theme_has_valid_menu_and_prompt_styles(name):
    active_theme.set(name)
    palette = current_palette()
    fragments = menu_view.render_menu(main._MENU_GROUPS, 0, 120, 50)
    styles = Style.from_dict({"": palette.base_style})

    for fragment_style, _ in fragments:
        styles.get_attrs_for_style_str(fragment_style)
    Style.from_dict(prompt_style().dict)
    assert f"Theme: {name.value}" in "".join(text for _, text in fragments)


@pytest.mark.parametrize("name", list(ThemeName))
def test_themes_keep_every_selection_visible_in_short_terminals(name):
    active_theme.set(name)
    options = [option for group in main._MENU_GROUPS for option in group.options]
    for selected, number in enumerate([option.number for option in options] + ["0"]):
        for height in (1, 2, 10):
            fragments = menu_view.render_menu(main._MENU_GROUPS, selected, 48, height)
            text = "".join(value for _, value in fragments)
            assert f"❯ [{number}]" in text
            assert len(text.splitlines()) <= height


def test_no_color_removes_palette_colors_but_keeps_selection(monkeypatch):
    active_theme.set(ThemeName.light)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("INQUIRERPY_STYLE_ANSWER", "#ff0000")

    fragments = menu_view.render_menu(main._MENU_GROUPS, 0, 120, 50)

    assert current_palette().name == ThemeName.monochrome
    assert all("fg:" not in style and "bg:" not in style for style, _ in fragments)
    assert any("reverse" in style for style, _ in fragments)
    assert all("#" not in style for style in prompt_style().dict.values())


def test_monochrome_detail_output_has_no_color_even_when_forced():
    active_theme.set(ThemeName.monochrome)

    app = typer.Typer()

    @app.command()
    def show():
        themed_echo("Detail panel")

    result = CliRunner().invoke(app, [], color=True)
    assert result.exit_code == 0
    assert "\x1b[" not in result.output


def _luminance(color):
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    channels = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return sum(
        value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722))
    )


def _contrast(first, second):
    low, high = sorted((_luminance(first), _luminance(second)))
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize(
    "name", [name for name in ThemeName if name != ThemeName.monochrome]
)
def test_palette_text_and_selected_options_meet_normal_text_contrast(name):
    palette = PALETTES[name]
    for role in (
        "foreground",
        "muted",
        "accent",
        "work",
        "success",
        "warning",
        "local",
        "error",
    ):
        assert _contrast(getattr(palette, role), palette.background) >= 4.5, (
            name,
            role,
        )
    for role in ("work", "success", "warning", "local", "error"):
        assert _contrast(palette.selection_foreground, getattr(palette, role)) >= 4.5, (
            name,
            role,
        )
