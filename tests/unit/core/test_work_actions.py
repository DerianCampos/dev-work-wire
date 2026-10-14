from unittest.mock import AsyncMock, Mock

import pytest

from devworkwire.core.domain.exceptions import (
    InvalidTransitionException,
    NotFoundException,
)
from devworkwire.core.ports.work_item_changes import (
    SearchFilters,
    StoryUpdate,
    TransitionSummary,
)
from devworkwire.features.progress.application.transitions import (
    ProgressActions,
    TransitionRequest,
    select_transition,
)
from devworkwire.features.workitem.application.manage_work import (
    assign_issue,
    search_jql,
    update_story,
)


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"title": "  "},
        {"priority": ""},
        {"labels": ("bad label",)},
        {"points": -1},
        {"epic_key": "not-a-key"},
        {"points": 0, "clear_points": True},
        {"epic_key": "PROJ-1", "clear_epic": True},
    ],
)
def test_story_patch_rejects_invalid_changes(options):
    with pytest.raises(ValueError):
        StoryUpdate(**options)


def test_explicit_empty_values_are_changes():
    update = StoryUpdate(description="", labels=(), points=0)
    assert update.description == "" and update.labels == () and update.points == 0
    assert update.title is None


@pytest.mark.asyncio
async def test_story_update_checks_story_and_parent_before_write():
    provider = Mock(
        get_issue_type=AsyncMock(side_effect=["Story", None]), update_story=AsyncMock()
    )
    with pytest.raises(NotFoundException, match="Epic"):
        await update_story(provider, "PROJ-2", StoryUpdate(epic_key="PROJ-1"))
    provider.update_story.assert_not_called()
    provider.get_issue_type.side_effect = ["Story", "Epic"]
    patch = StoryUpdate(epic_key="PROJ-1")
    await update_story(provider, "PROJ-2", patch)
    provider.update_story.assert_awaited_once_with("PROJ-2", patch)


@pytest.mark.asyncio
async def test_missing_story_prevents_parent_lookup_and_write():
    provider = Mock(
        get_issue_type=AsyncMock(return_value=None), update_story=AsyncMock()
    )
    with pytest.raises(NotFoundException):
        await update_story(provider, "PROJ-2", StoryUpdate(epic_key="PROJ-1"))
    provider.get_issue_type.assert_awaited_once_with("PROJ-2")
    provider.update_story.assert_not_called()


@pytest.mark.asyncio
async def test_assignment_resolves_current_user_and_preserves_unassigned():
    provider = Mock(
        current_account_id=AsyncMock(return_value="557058:abc"),
        assign_issue=AsyncMock(),
    )
    assert await assign_issue(provider, "PROJ-2", "me") == "557058:abc"
    provider.assign_issue.assert_awaited_with("PROJ-2", "557058:abc")
    assert await assign_issue(provider, "PROJ-2", None) is None
    provider.assign_issue.assert_awaited_with("PROJ-2", None)


def test_search_combines_project_or_filters_and_all_labels():
    query = search_jql(
        SearchFilters(
            statuses=("To Do", "In Progress"),
            labels=("backend", "urgent"),
            issue_types=("Story", "Bug"),
        ),
        "other",
    )
    assert query == (
        'project = "OTHER" AND status IN ("To Do", "In Progress") '
        'AND issuetype IN ("Story", "Bug") AND labels = "backend" AND labels = "urgent" '
        "ORDER BY updated DESC, key ASC"
    )


def test_filter_literals_cannot_escape_their_quotes():
    query = search_jql(SearchFilters(statuses=('Open" OR project = OTHER \\',)), "PROJ")
    assert 'status IN ("Open\\" OR project = OTHER \\\\")' in query


def test_raw_jql_is_unchanged_and_filterless_search_has_no_open_only_clause():
    raw = "project IN (PROJ, OTHER) ORDER BY key DESC"
    assert search_jql(SearchFilters(jql=raw), "PROJ") == raw
    assert (
        search_jql(SearchFilters(), "PROJ")
        == 'project = "PROJ" ORDER BY updated DESC, key ASC'
    )


@pytest.mark.parametrize(
    "options",
    [
        {"jql": " "},
        {"jql": "project = OTHER", "labels": ("urgent",)},
        {"statuses": ("",)},
        {"issue_types": ("Story\nOR project = OTHER",)},
    ],
)
def test_search_rejects_conflicts_and_invalid_filter_values(options):
    with pytest.raises(ValueError):
        SearchFilters(**options)


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"status": ""},
        {"transition_id": "abc"},
        {"status": "Done", "transition_id": "31"},
    ],
)
def test_transition_requires_one_valid_selector(options):
    with pytest.raises(ValueError):
        TransitionRequest(**options)


def test_status_matches_case_insensitively_and_aliases_match_action_names():
    transitions = [TransitionSummary("31", "Start Progress", "In Progress")]
    assert (
        select_transition(transitions, TransitionRequest(status=" in progress "), {})
        == transitions[0]
    )
    assert (
        select_transition(
            transitions, TransitionRequest(status="start"), {"start": "Start Progress"}
        )
        == transitions[0]
    )
    assert (
        select_transition(
            transitions, TransitionRequest(transition_id="31"), {"start": "Other"}
        )
        == transitions[0]
    )


@pytest.mark.asyncio
async def test_ambiguous_or_missing_status_never_writes_and_lists_choices():
    transitions = [
        TransitionSummary("31", "Finish", "Done"),
        TransitionSummary("32", "Close", "Done"),
    ]
    provider = Mock(
        list_transitions=AsyncMock(return_value=transitions),
        transition_issue=AsyncMock(),
    )
    progress = ProgressActions(provider, {})
    for status in ("Done", "Unknown"):
        with pytest.raises(InvalidTransitionException, match="31: Finish"):
            await progress.change_status("PROJ-2", TransitionRequest(status=status))
    provider.transition_issue.assert_not_called()
    selected = await progress.change_status(
        "PROJ-2", TransitionRequest(transition_id="32")
    )
    assert selected == transitions[1]
    provider.transition_issue.assert_awaited_once_with("PROJ-2", "32")
