from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from devworkwire.core.ports.work_item_changes import (
    SearchFilters,
    StoryUpdate,
    TransitionSummary,
)
from devworkwire.features.progress.application.transitions import TransitionRequest
from devworkwire.presentation.cli import main, work_menu


def _prompt(value):
    return SimpleNamespace(execute=lambda: value)


def _script(monkeypatch, choices, texts=(), confirmed=True, fields=()):
    selected = iter(choices)
    entered = iter(texts)
    monkeypatch.setattr(
        work_menu.inquirer, "select", lambda **_: _prompt(next(selected))
    )
    monkeypatch.setattr(work_menu.inquirer, "text", lambda **_: _prompt(next(entered)))
    monkeypatch.setattr(work_menu.inquirer, "confirm", lambda **_: _prompt(confirmed))
    monkeypatch.setattr(work_menu.inquirer, "checkbox", lambda **_: _prompt(fields))


@pytest.fixture
def actions():
    return SimpleNamespace(
        update=AsyncMock(),
        assign=AsyncMock(),
        transition=AsyncMock(),
        transitions=AsyncMock(),
        available_transitions=AsyncMock(
            return_value=[TransitionSummary("31", "Start", "In Progress")]
        ),
        search=AsyncMock(),
    )


def test_story_menu_updates_only_selected_fields(monkeypatch, actions):
    _script(monkeypatch, ["Edit story"], ["PROJ-2", "New title"], fields=["title"])
    assert work_menu.run_work_menu(actions)
    actions.update.assert_awaited_once_with("PROJ-2", StoryUpdate(title="New title"))


def test_story_menu_can_clear_optional_fields(monkeypatch, actions):
    _script(
        monkeypatch,
        ["Edit story"],
        ["PROJ-2", "", "", "", ""],
        fields=["description", "labels", "points", "epic_key"],
    )
    work_menu.run_work_menu(actions)
    actions.update.assert_awaited_once_with(
        "PROJ-2",
        StoryUpdate(description="", labels=(), clear_points=True, clear_epic=True),
    )


def test_story_edit_with_no_fields_returns_without_write(monkeypatch, actions):
    _script(monkeypatch, ["Edit story"], ["PROJ-2"])
    assert not work_menu.run_work_menu(actions)
    actions.update.assert_not_called()


@pytest.mark.parametrize(
    "choice,texts,fields",
    [
        ("Edit story", ["PROJ-2", "New title"], ["title"]),
        ("Assign issue", ["PROJ-2"], []),
        ("Change status", ["PROJ-2"], []),
    ],
)
def test_declined_menu_confirmation_never_writes(
    monkeypatch, actions, choice, texts, fields
):
    choices = [choice]
    if choice == "Assign issue":
        choices.append("Current user")
    elif choice == "Change status":
        choices.append("31")
    _script(monkeypatch, choices, texts, confirmed=False, fields=fields)
    work_menu.run_work_menu(actions)
    actions.update.assert_not_called()
    actions.assign.assert_not_called()
    actions.transition.assert_not_called()


@pytest.mark.parametrize(
    "mode,texts,assignee,unassign",
    [
        ("Current user", ["PROJ-2"], "me", False),
        ("Account ID", ["PROJ-2", "557058:abc"], "557058:abc", False),
        ("Unassigned", ["PROJ-2"], None, True),
    ],
)
def test_assignment_menu_modes(monkeypatch, actions, mode, texts, assignee, unassign):
    _script(monkeypatch, ["Assign issue", mode], texts)
    work_menu.run_work_menu(actions)
    actions.assign.assert_awaited_once_with("PROJ-2", assignee, unassign)


def test_status_menu_uses_selected_real_transition_id(monkeypatch, actions):
    _script(monkeypatch, ["Change status", "31"], ["PROJ-2"])
    work_menu.run_work_menu(actions)
    actions.available_transitions.assert_awaited_once_with("PROJ-2")
    actions.transition.assert_awaited_once_with(
        "PROJ-2", TransitionRequest(transition_id="31")
    )


def test_no_available_transitions_does_not_write(monkeypatch, actions, capsys):
    _script(monkeypatch, ["Change status"], ["PROJ-2"])
    actions.available_transitions.return_value = []
    work_menu.run_work_menu(actions)
    assert "No transitions" in capsys.readouterr().out
    actions.transition.assert_not_called()


@pytest.mark.parametrize(
    "mode,texts,filters",
    [
        (
            "Filters",
            ["To Do,In Progress", "backend,urgent", "Story,Bug"],
            SearchFilters(
                ("To Do", "In Progress"), ("backend", "urgent"), ("Story", "Bug")
            ),
        ),
        (
            "Raw JQL",
            ["project IN (PROJ, OTHER)"],
            SearchFilters(jql="project IN (PROJ, OTHER)"),
        ),
    ],
)
def test_search_menu_uses_selected_mode(monkeypatch, actions, mode, texts, filters):
    _script(monkeypatch, [mode], texts)
    assert work_menu.run_search_menu(actions)
    actions.search.assert_awaited_once_with(filters)


def test_management_and_search_back_do_nothing(monkeypatch, actions):
    _script(monkeypatch, ["Back", "Back"])
    assert not work_menu.run_work_menu(actions)
    assert not work_menu.run_search_menu(actions)
    actions.update.assert_not_called()
    actions.search.assert_not_called()


def test_ctrl_c_returns_to_menu_without_write(monkeypatch, actions):
    monkeypatch.setattr(
        work_menu.inquirer, "select", Mock(side_effect=KeyboardInterrupt)
    )
    assert not work_menu.run_work_menu(actions)
    assert not work_menu.run_search_menu(actions)
    actions.update.assert_not_called()


def test_invalid_points_are_reported_without_write(monkeypatch, actions, capsys):
    _script(monkeypatch, ["Edit story"], ["PROJ-2", "many"], fields=["points"])
    assert work_menu.run_work_menu(actions)
    assert "Error managing work" in capsys.readouterr().err
    actions.update.assert_not_called()


@pytest.mark.parametrize(
    "choice,handler",
    [(main._SEARCH, "run_search_menu"), (main._MANAGE, "run_work_menu")],
)
def test_main_menu_routes_new_actions(monkeypatch, choice, handler):
    choices = iter([choice, main._EXIT])
    monkeypatch.setattr(main, "run_menu", lambda *_: next(choices))
    action = Mock(return_value=False)
    monkeypatch.setattr(main, handler, action)
    main._run_interactive_menu()
    action.assert_called_once_with(main.work_actions)
