import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from devworkwire.config.project_config import ProjectConfig
from devworkwire.infrastructure.external.jira.jira_provider import JiraProvider
from devworkwire.infrastructure.external.jira.settings import JiraSettings
from devworkwire.presentation.cli import main

runner = CliRunner()
BASE = "https://jira.test/rest/api/3"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    main.container.select_project(None)
    monkeypatch.setattr(
        main.container,
        "_jira_provider",
        JiraProvider(
            JiraSettings(
                "https://jira.test",
                "user",
                "test-token",
                "PROJ",
                max_retries=1,
                story_points_field="customfield_99999",
            )
        ),
    )
    monkeypatch.setattr(
        main.container,
        "resolve_jira_connection",
        lambda: (
            None,
            ProjectConfig("jira", transition_overrides={"start": "Start Progress"}),
        ),
    )


def invoke(*arguments):
    result = runner.invoke(main.app, ["--format", "json", *arguments])
    return result, json.loads(result.stdout)


def story(issue_type="Story"):
    respx.get(f"{BASE}/issue/PROJ-2").respond(
        200,
        json={
            "key": "PROJ-2",
            "fields": {
                "issuetype": {"name": issue_type},
                "summary": "Original",
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "unsupported"}],
                },
            },
        },
    )


def transitions(duplicate=False):
    choices = [{"id": "31", "name": "Start Progress", "to": {"name": "In Progress"}}]
    if duplicate:
        choices.append({"id": "32", "name": "Resume", "to": {"name": "In Progress"}})
    respx.get(f"{BASE}/issue/PROJ-2/transitions").respond(
        200, json={"transitions": choices}
    )


@respx.mock
def test_update_json_preserves_unselected_fields_and_reports_changes():
    story()
    route = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    result, payload = invoke("update-story", "PROJ-2", "--title", "Revised")
    assert result.exit_code == 0, result.output
    assert payload == {
        "command": "update-story",
        "status": "completed",
        "data": {"key": "PROJ-2", "changed_fields": ["title"]},
        "error": None,
    }
    assert json.loads(route.calls.last.request.content) == {
        "fields": {"summary": "Revised"}
    }


@respx.mock
def test_update_clearing_flags_and_replacement_labels():
    story()
    route = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    result, payload = invoke(
        "update-story",
        "PROJ-2",
        "--description",
        "",
        "--clear-labels",
        "--clear-points",
        "--clear-epic",
    )
    assert result.exit_code == 0
    assert set(payload["data"]["changed_fields"]) == {
        "description",
        "labels",
        "points",
        "epic_key",
    }
    assert json.loads(route.calls.last.request.content)["fields"] == {
        "description": None,
        "labels": [],
        "customfield_99999": None,
    }
    result, _ = invoke(
        "update-story", "PROJ-2", "--label", "backend", "--label", "urgent"
    )
    assert result.exit_code == 0
    assert json.loads(route.calls.last.request.content)["fields"] == {
        "labels": ["backend", "urgent"]
    }


@respx.mock
@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--title", " "],
        ["--points", "-1"],
        ["--label", "bad label"],
        ["--label", "urgent", "--clear-labels"],
        ["--points", "1", "--clear-points"],
        ["--epic", "PROJ-1", "--clear-epic"],
    ],
)
def test_invalid_story_edit_arguments_return_json_and_do_not_contact_jira(arguments):
    result, payload = invoke("update-story", "PROJ-2", *arguments)
    assert result.exit_code != 0
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert not respx.calls


@respx.mock
def test_story_update_rejects_wrong_issue_type_without_writing():
    story("Bug")
    write = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    result, payload = invoke("update-story", "PROJ-2", "--title", "Revised")
    assert result.exit_code == 1
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert not write.called


@respx.mock
@pytest.mark.parametrize(
    "arguments,fields",
    [
        (["--title", "Revised"], {"summary": "Revised"}),
        (["--clear-points"], {"customfield_99999": None}),
    ],
)
def test_editing_story_does_not_parse_existing_fractional_points(arguments, fields):
    read = respx.get(f"{BASE}/issue/PROJ-2").respond(
        200,
        json={
            "key": "PROJ-2",
            "fields": {
                "issuetype": {"name": "Story"},
                "summary": "Original",
                "customfield_99999": 0.5,
            },
        },
    )
    write = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    result, payload = invoke("update-story", "PROJ-2", *arguments)
    assert result.exit_code == 0, result.output
    assert payload["status"] == "completed"
    assert read.calls.last.request.url.params["fields"] == "issuetype"
    assert json.loads(write.calls.last.request.content) == {"fields": fields}


@respx.mock
def test_missing_parent_prevents_story_update():
    story()
    respx.get(f"{BASE}/issue/PROJ-1").respond(404)
    write = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    result, payload = invoke("update-story", "PROJ-2", "--epic", "PROJ-1")
    assert result.exit_code == 1
    assert payload["error"]["code"] == "NOT_FOUND"
    assert not write.called


@respx.mock
@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["--assignee", "557058:abc"], "557058:abc"),
        (["--assignee", "me"], "current-id"),
        (["--unassign"], None),
    ],
)
def test_assignment_json_contract(arguments, expected):
    respx.get(f"{BASE}/myself").respond(200, json={"accountId": "current-id"})
    write = respx.put(f"{BASE}/issue/PROJ-2/assignee").respond(204)
    result, payload = invoke("assign", "PROJ-2", *arguments)
    assert result.exit_code == 0, result.output
    assert payload["data"] == {"key": "PROJ-2", "assignee": expected}
    assert json.loads(write.calls.last.request.content) == {"accountId": expected}


@respx.mock
@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["--assignee", "abc", "--unassign"],
        ["--assignee", "person@example.com"],
        ["--assignee", ""],
    ],
)
def test_invalid_assignment_never_writes(arguments):
    result, payload = invoke("assign", "PROJ-2", *arguments)
    assert result.exit_code == 1
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert not respx.calls


@respx.mock
@pytest.mark.parametrize(
    "selector",
    [["--status", "in progress"], ["--status", "start"], ["--transition-id", "31"]],
)
def test_status_or_id_or_alias_resolves_transition(selector):
    transitions()
    write = respx.post(f"{BASE}/issue/PROJ-2/transitions").respond(204)
    result, payload = invoke("transition", "PROJ-2", *selector)
    assert result.exit_code == 0, result.output
    assert payload["data"]["transition"]["status"] == "In Progress"
    assert json.loads(write.calls.last.request.content) == {"transition": {"id": "31"}}


@respx.mock
def test_list_transitions_json_and_ambiguity_prevent_write():
    transitions(duplicate=True)
    write = respx.post(f"{BASE}/issue/PROJ-2/transitions").respond(204)
    result, payload = invoke("list-transitions", "PROJ-2")
    assert result.exit_code == 0
    assert [transition["id"] for transition in payload["data"]["transitions"]] == [
        "31",
        "32",
    ]
    result, payload = invoke("transition", "PROJ-2", "--status", "In Progress")
    assert result.exit_code == 1
    assert payload["error"]["code"] == "INVALID_TRANSITION"
    assert "31" in payload["error"]["message"] and "32" in payload["error"]["message"]
    assert not write.called


@respx.mock
@pytest.mark.parametrize(
    "status,code",
    [
        (400, "JIRA_ERROR"),
        (401, "AUTHENTICATION_FAILED"),
        (403, "PERMISSION_DENIED"),
        (404, "NOT_FOUND"),
        (503, "WRITE_UNCERTAIN"),
    ],
)
def test_write_errors_have_stable_codes_and_never_report_success(status, code):
    write = respx.put(f"{BASE}/issue/PROJ-2/assignee").respond(
        status, json={"errors": {"assignee": "Rejected"}}
    )
    result, payload = invoke("assign", "PROJ-2", "--assignee", "abc")
    assert result.exit_code == 1
    assert payload["status"] == "error" and payload["error"]["code"] == code
    assert write.call_count == 1


@respx.mock
def test_timeout_is_uncertain_and_has_manual_inspection_guidance():
    write = respx.put(f"{BASE}/issue/PROJ-2/assignee").mock(
        side_effect=httpx.ReadTimeout("timeout")
    )
    result, payload = invoke("assign", "PROJ-2", "--unassign")
    assert result.exit_code == 1
    assert payload["error"]["code"] == "WRITE_UNCERTAIN"
    assert "Inspect the issue" in payload["error"]["message"]
    assert write.call_count == 1


@respx.mock
def test_search_json_filters_and_raw_jql():
    route = respx.post(f"{BASE}/search/jql").respond(
        200,
        json={
            "issues": [
                {
                    "key": "OTHER-1",
                    "fields": {
                        "summary": "Fix",
                        "issuetype": {"name": "Bug"},
                        "status": {"name": "Done"},
                    },
                }
            ],
            "isLast": True,
        },
    )
    result, payload = invoke(
        "search",
        "--status",
        "Done",
        "--type",
        "Bug",
        "--label",
        "backend",
        "--label",
        "urgent",
    )
    assert result.exit_code == 0
    assert payload["data"]["items"] == [
        {"key": "OTHER-1", "title": "Fix", "issue_type": "Bug", "status": "Done"}
    ]
    query = payload["data"]["jql"]
    assert (
        'project = "PROJ"' in query
        and 'labels = "backend" AND labels = "urgent"' in query
    )
    assert json.loads(route.calls.last.request.content)["jql"] == query
    raw = "project IN (OTHER, THIRD) ORDER BY key DESC"
    result, payload = invoke("search", "--jql", raw)
    assert result.exit_code == 0
    assert payload["data"]["jql"] == raw
    assert json.loads(route.calls.last.request.content)["jql"] == raw


@respx.mock
@pytest.mark.parametrize(
    "arguments", [["--jql", " "], ["--jql", "project = OTHER", "--status", "Done"]]
)
def test_invalid_search_returns_json_without_http(arguments):
    result, payload = invoke("search", *arguments)
    assert result.exit_code != 0
    assert payload["error"]["code"] == "INVALID_ARGUMENT"
    assert not respx.calls


@respx.mock
def test_empty_search_and_text_write_succeed():
    respx.post(f"{BASE}/search/jql").respond(200, json={"issues": [], "isLast": True})
    result = runner.invoke(main.app, ["search"])
    assert result.exit_code == 0 and "No matching work items" in result.stdout
    respx.put(f"{BASE}/issue/PROJ-2/assignee").respond(204)
    result = runner.invoke(main.app, ["assign", "PROJ-2", "--unassign"])
    assert result.exit_code == 0 and "unassigned" in result.stdout


@respx.mock
def test_required_transition_field_failure_is_actionable():
    transitions()
    respx.post(f"{BASE}/issue/PROJ-2/transitions").respond(
        400, json={"errors": {"resolution": "Required"}}
    )
    result, payload = invoke("transition", "PROJ-2", "--transition-id", "31")
    assert result.exit_code == 1
    assert "resolution: Required" in payload["error"]["message"]
