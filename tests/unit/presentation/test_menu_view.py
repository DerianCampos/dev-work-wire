from prompt_toolkit.application import Application
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from devworkwire.presentation.cli import main, menu_view


def _plain_text(width: int, height: int, selected: int = 0, show_help: bool = False) -> str:
    fragments = menu_view.render_menu(
        main._MENU_GROUPS, selected, width, height, show_help
    )
    return "".join(text for _, text in fragments)


def test_full_menu_uses_grouped_panels_and_provider_neutral_wording():
    screen = _plain_text(120, 50)

    assert "╭" in screen
    assert "WORK ITEMS" in screen
    assert "CREATE" in screen
    assert "IMPORT" in screen
    assert "LOCAL" in screen
    assert "Backlog Workflow Assistant" in screen
    assert "Jira" not in screen
    assert "[7]  Import epic and stories from folder" in screen
    assert "[8]  Preview and validate folder" in screen
    assert "[9]  Configure connection and projects" in screen
    assert all(len(line) <= 120 for line in screen.splitlines())


def test_compact_menu_keeps_all_shortcuts_visible():
    screen = _plain_text(48, 24)

    assert "╭" not in screen
    assert all(f"[{number}]" in screen for number in range(10))
    assert "[s]" in screen and "[w]" in screen
    assert all(len(line) <= 48 for line in screen.splitlines())


def test_very_narrow_menu_does_not_exceed_terminal_width():
    screen = _plain_text(18, 24)

    assert all(len(line) <= 18 for line in screen.splitlines())


def test_short_menu_keeps_each_selected_action_visible():
    shortcuts = [option.number for group in main._MENU_GROUPS for option in group.options] + ["0"]
    for selected, number in enumerate(shortcuts):
        screen = _plain_text(80, 10, selected)
        assert len(screen.splitlines()) <= 10
        assert f"❯ [{number}]" in screen


def test_short_help_keeps_controls_visible():
    screen = _plain_text(80, 10, show_help=True)

    assert len(screen.splitlines()) <= 10
    assert "Esc      Return to the menu" in screen


def test_narrow_help_respects_terminal_width():
    screen = _plain_text(18, 10, show_help=True)

    assert all(len(line) <= 18 for line in screen.splitlines())


def test_very_short_help_shows_back_control():
    screen = _plain_text(30, 3, show_help=True)

    assert len(screen.splitlines()) <= 3
    assert "Esc Back" in screen


def test_help_lists_keyboard_controls_and_actions():
    screen = _plain_text(80, 24, show_help=True)

    assert "Keyboard help" in screen
    assert "Esc      Return to the menu" in screen
    assert "[0] Exit" in screen
    assert "[8] Preview and validate folder" in screen
    assert "[9] Configure connection and projects" in screen
    assert "0–9" in screen


def test_help_never_exceeds_terminal_height():
    for height in range(3, 30):
        screen = _plain_text(80, height, show_help=True)
        assert len(screen.splitlines()) <= height, height
    required_height = 8 + sum(len(group.options) for group in main._MENU_GROUPS)
    assert "[9] Configure connection and projects" in _plain_text(80, required_height, show_help=True)


def _press_keys(monkeypatch, keys: str) -> str:
    original_application = Application
    with create_pipe_input() as pipe_input:
        monkeypatch.setattr(
            menu_view,
            "Application",
            lambda **options: original_application(
                input=pipe_input, output=DummyOutput(), **options
            ),
        )
        pipe_input.send_text(keys)
        return menu_view.run_menu(main._MENU_GROUPS, main._EXIT)


def test_number_shortcut_selects_action(monkeypatch):
    assert _press_keys(monkeypatch, "7") == main._IMPORT_FOLDER


def test_arrow_keys_and_enter_select_highlighted_action(monkeypatch):
    assert _press_keys(monkeypatch, "\x1b[B\r") == main._FETCH_STORY


def test_help_does_not_trigger_shortcuts_until_closed(monkeypatch):
    assert _press_keys(monkeypatch, "?7\x1b8") == main._PREVIEW_FOLDER


def test_nine_shortcut_selects_configure(monkeypatch):
    assert _press_keys(monkeypatch, "9") == main._CONFIGURE


def test_letter_shortcuts_select_new_actions(monkeypatch):
    assert _press_keys(monkeypatch, "s") == main._SEARCH
    assert _press_keys(monkeypatch, "w") == main._MANAGE


def test_zero_exits_menu(monkeypatch):
    assert _press_keys(monkeypatch, "0") == main._EXIT


def test_escape_exits_main_menu(monkeypatch):
    assert _press_keys(monkeypatch, "\x1b") == main._EXIT
