from unittest.mock import AsyncMock, MagicMock

import pytest

from devworkwire.presentation.cli import main


def _prompt(value):
    prompt = MagicMock()
    prompt.execute.return_value = value
    return prompt


def _menu(monkeypatch, choice, answers):
    selected = iter([choice, main._EXIT])
    entered = iter(answers)
    monkeypatch.setattr(main, "run_menu", MagicMock(side_effect=lambda *_: next(selected)))
    monkeypatch.setattr(
        main.inquirer, "text", MagicMock(side_effect=lambda **_: _prompt(next(entered)))
    )
    monkeypatch.setattr(main.inquirer, "confirm", MagicMock(return_value=_prompt(True)))


def test_exit_choice_stops_the_menu_without_calling_any_action(monkeypatch):
    monkeypatch.setattr(main, "run_menu", MagicMock(return_value=main._EXIT))
    clear = MagicMock()
    monkeypatch.setattr(main.click, "clear", clear)
    fetch = AsyncMock()
    monkeypatch.setattr(main, "_fetch_epic", fetch)

    main._run_interactive_menu()

    fetch.assert_not_called()
    clear.assert_not_called()


def test_selection_clears_before_prompt_and_result(monkeypatch):
    events = []
    selected = iter([main._FETCH_STORY, main._EXIT])
    monkeypatch.setattr(
        main, "run_menu", lambda *_: events.append("menu") or next(selected)
    )
    monkeypatch.setattr(main.click, "clear", lambda: events.append("clear"))
    prompt = MagicMock()
    prompt.execute.side_effect = lambda: events.append("prompt") or "PROJ-1"
    monkeypatch.setattr(main.inquirer, "text", MagicMock(return_value=prompt))
    monkeypatch.setattr(main, "_fetch_story", AsyncMock(side_effect=lambda _: events.append("result")))
    returned = MagicMock()
    returned.execute.side_effect = lambda: events.append("return")
    monkeypatch.setattr(main.inquirer, "confirm", MagicMock(return_value=returned))

    main._run_interactive_menu()

    assert events == ["menu", "clear", "prompt", "result", "return", "menu"]


@pytest.mark.parametrize("choice,target", [
    (main._FETCH_EPIC, "_fetch_epic"),
    (main._FETCH_STORY, "_fetch_story"),
    (main._LIST_STORIES, "_list_stories"),
    (main._IMPORT_FOLDER, "_import_folder"),
])
def test_key_and_folder_menu_actions(monkeypatch, choice, target):
    _menu(monkeypatch, choice, ["PROJ-1"])
    action = AsyncMock()
    monkeypatch.setattr(main, target, action)

    main._run_interactive_menu()

    action.assert_awaited_once_with("PROJ-1")


def test_preview_folder_menu_uses_read_only_action(monkeypatch):
    _menu(monkeypatch, main._PREVIEW_FOLDER, ["work-items"])
    preview = MagicMock()
    monkeypatch.setattr(main, "_preview_folder", preview)

    main._run_interactive_menu()

    preview.assert_called_once_with("work-items")


@pytest.mark.parametrize("entered,expected", [
    ("", None),
    ("   ", None),
    ("557058:abcd-1234", "557058:abcd-1234"),
    (" 557058:abcd-1234 ", "557058:abcd-1234"),
])
def test_list_assigned_menu_uses_current_or_explicit_user(monkeypatch, entered, expected):
    _menu(monkeypatch, main._LIST_ASSIGNED, [entered])
    action = AsyncMock()
    monkeypatch.setattr(main, "_list_assigned", action)

    main._run_interactive_menu()

    action.assert_awaited_once_with(expected)


def test_create_epic_menu_prompts_for_fields(monkeypatch):
    _menu(monkeypatch, main._CREATE_EPIC, ["Authentication", "Login work", "High", "auth, backend"])
    create = AsyncMock()
    monkeypatch.setattr(main, "_create_epic", create)

    main._run_interactive_menu()

    create.assert_awaited_once_with(main.WorkItemInput(
        title="Authentication", description="Login work", priority="High",
        labels=("auth", "backend"),
    ))


def test_create_story_menu_prompts_for_parent_and_points(monkeypatch):
    _menu(monkeypatch, main._CREATE_STORY, ["PROJ-1", "Login", "Description", "High", "auth", "5"])
    create = AsyncMock()
    monkeypatch.setattr(main, "_create_story", create)

    main._run_interactive_menu()

    create.assert_awaited_once_with("PROJ-1", main.WorkItemInput(
        title="Login", description="Description", priority="High",
        labels=("auth",), points=5,
    ))


def test_create_story_menu_rejects_invalid_points(monkeypatch, capsys):
    _menu(monkeypatch, main._CREATE_STORY, ["PROJ-1", "Login", "", "", "", "many"])
    create = AsyncMock()
    monkeypatch.setattr(main, "_create_story", create)

    main._run_interactive_menu()

    create.assert_not_called()
    assert "Invalid input" in capsys.readouterr().err


def _configure(monkeypatch, selections, answers=(), stored_projects=()):
    """Run the Configure sub-menu with scripted select/text/confirm answers."""
    from devworkwire.core.ports.jira_config_store import JiraProject
    _menu(monkeypatch, main._CONFIGURE, list(answers))
    picks = iter(selections)
    select = MagicMock(side_effect=lambda **_: _prompt(next(picks)))
    monkeypatch.setattr(main.inquirer, "select", select)
    monkeypatch.setattr(main.container.config_store, "list_projects", lambda: [
        JiraProject(key, index == 0) for index, key in enumerate(stored_projects)
    ])
    return select


def test_configure_show_prints_current_settings(monkeypatch):
    _configure(monkeypatch, [main._CONFIG_SHOW])
    show = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_show", show)

    main._run_interactive_menu()

    show.assert_called_once_with()


def test_configure_setup_runs_the_prompting_setup(monkeypatch):
    _configure(monkeypatch, [main._CONFIG_SETUP])
    setup = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_setup", setup)

    main._run_interactive_menu()

    setup.assert_called_once_with(None, None, None)


def test_configure_add_project_asks_key_and_default(monkeypatch):
    _configure(monkeypatch, [main._CONFIG_ADD_PROJECT], ["web"])
    monkeypatch.setattr(main.inquirer, "confirm", MagicMock(return_value=_prompt(True)))
    projects = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_projects", projects)

    main._run_interactive_menu()

    projects.assert_called_once_with("add", "web", True)


def test_configure_set_default_offers_stored_projects(monkeypatch):
    select = _configure(monkeypatch, [main._CONFIG_SET_DEFAULT, "WEB"], stored_projects=("PROJ", "WEB"))
    projects = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_projects", projects)

    main._run_interactive_menu()

    projects.assert_called_once_with("default", "WEB")
    choices = select.call_args_list[1].kwargs["choices"]
    assert [(choice.value, choice.name) for choice in choices] == [
        ("PROJ", "PROJ (default)"), ("WEB", "WEB"), (None, main._CONFIG_BACK),
    ]


def test_configure_remove_project_uses_the_project_key(monkeypatch):
    _configure(monkeypatch, [main._CONFIG_REMOVE_PROJECT, "PROJ"], stored_projects=("PROJ",))
    projects = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_projects", projects)

    main._run_interactive_menu()

    projects.assert_called_once_with("remove", "PROJ")


def test_configure_project_picker_without_projects_explains_and_pauses(monkeypatch, capsys):
    _configure(monkeypatch, [main._CONFIG_SET_DEFAULT])
    monkeypatch.setattr(main, "_config_projects", MagicMock(side_effect=AssertionError))

    main._run_interactive_menu()

    assert "No projects configured yet" in capsys.readouterr().out
    assert main.inquirer.confirm.call_count == 1


def test_configure_project_picker_back_changes_nothing_and_skips_the_pause(monkeypatch):
    _configure(monkeypatch, [main._CONFIG_REMOVE_PROJECT, None], stored_projects=("PROJ",))
    projects = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_projects", projects)

    main._run_interactive_menu()

    projects.assert_not_called()
    main.inquirer.confirm.assert_not_called()


def test_configure_project_picker_reports_unreadable_store(monkeypatch, capsys):
    _configure(monkeypatch, [main._CONFIG_SET_DEFAULT])
    monkeypatch.setattr(
        main.container.config_store, "list_projects",
        MagicMock(side_effect=RuntimeError("file is not a database")),
    )

    main._run_interactive_menu()

    assert "file is not a database" in capsys.readouterr().err


def test_configure_add_project_rejects_invalid_key_before_asking_about_default(monkeypatch, capsys):
    _configure(monkeypatch, [main._CONFIG_ADD_PROJECT], ["1-bad"])
    projects = MagicMock(return_value=True)
    monkeypatch.setattr(main, "_config_projects", projects)

    main._run_interactive_menu()

    projects.assert_not_called()
    assert "project key must start with a letter" in capsys.readouterr().err
    main.inquirer.confirm.assert_called_once()  # only the "press Enter" pause


def test_configure_back_skips_the_pause_and_does_nothing(monkeypatch):
    _configure(monkeypatch, [main._CONFIG_BACK])
    for name in ("_config_show", "_config_setup", "_config_projects"):
        monkeypatch.setattr(main, name, MagicMock(side_effect=AssertionError(name)))

    main._run_interactive_menu()

    main.inquirer.confirm.assert_not_called()


def test_ctrl_c_in_the_configure_submenu_returns_to_the_main_menu(monkeypatch):
    _configure(monkeypatch, [])
    monkeypatch.setattr(main.inquirer, "select", MagicMock(side_effect=KeyboardInterrupt))

    main._run_interactive_menu()

    main.inquirer.confirm.assert_not_called()


def test_configure_theme_saves_and_applies_the_choice(monkeypatch):
    from devworkwire.config.theme_config import ThemeName, load_theme
    from devworkwire.presentation.cli.theme import active_theme

    _configure(monkeypatch, [main._CONFIG_THEME, ThemeName.light])
    token = active_theme.set(ThemeName.dark)
    try:
        main._run_interactive_menu()

        assert load_theme() == ThemeName.light
        assert active_theme.get() == ThemeName.light
    finally:
        active_theme.reset(token)


def test_configure_theme_back_keeps_preference_and_skips_pause(monkeypatch):
    from devworkwire.config.theme_config import ThemeName, load_theme, save_theme

    save_theme(ThemeName.dracula)
    _configure(monkeypatch, [main._CONFIG_THEME, None])

    main._run_interactive_menu()

    assert load_theme() == ThemeName.dracula
    main.inquirer.confirm.assert_not_called()
