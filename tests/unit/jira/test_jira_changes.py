import json
from dataclasses import replace

import httpx
import pytest
import respx

from devworkwire.core.domain.exceptions import UncertainWriteException
from devworkwire.core.ports.work_item_changes import StoryUpdate
from devworkwire.infrastructure.external.jira.jira_provider import JiraProvider
from devworkwire.infrastructure.external.jira.settings import JiraSettings

BASE = "https://jira.test/rest/api/3"


@pytest.fixture
def provider():
    return JiraProvider(
        JiraSettings(
            "https://jira.test",
            "user",
            "test-token",
            "PROJ",
            max_retries=1,
            story_points_field="customfield_99999",
        )
    )


@pytest.mark.asyncio
@respx.mock
async def test_partial_patch_only_sends_explicit_fields(provider):
    route = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    await provider.update_story("PROJ-2", StoryUpdate(title="New title"))
    assert json.loads(route.calls.last.request.content) == {
        "fields": {"summary": "New title"}
    }


@pytest.mark.asyncio
@respx.mock
async def test_patch_maps_markdown_priority_labels_zero_points_and_parent(provider):
    route = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    await provider.update_story(
        "PROJ-2",
        StoryUpdate(
            description="**Bold**",
            priority="Must Have",
            labels=("backend",),
            points=0,
            epic_key="PROJ-1",
        ),
    )
    fields = json.loads(route.calls.last.request.content)["fields"]
    assert fields["description"]["type"] == "doc"
    assert fields["description"]["content"][0]["content"][0]["marks"] == [
        {"type": "strong"}
    ]
    assert fields["priority"] == {"name": "Highest"}
    assert fields["labels"] == ["backend"]
    assert fields["customfield_99999"] == 0
    assert fields["parent"] == {"key": "PROJ-1"}


@pytest.mark.asyncio
@respx.mock
async def test_patch_clears_fields_using_jira_wire_contract(provider):
    route = respx.put(f"{BASE}/issue/PROJ-2").respond(204)
    await provider.update_story(
        "PROJ-2",
        StoryUpdate(
            description="",
            labels=(),
            clear_points=True,
            clear_epic=True,
        ),
    )
    assert json.loads(route.calls.last.request.content) == {
        "fields": {"description": None, "labels": [], "customfield_99999": None},
        "update": {"parent": [{"set": {"none": True}}]},
    }


@pytest.mark.asyncio
@respx.mock
async def test_points_require_configured_field_without_write(provider):
    provider = JiraProvider(replace(provider.settings, story_points_field=None))
    with pytest.raises(ValueError, match="not configured"):
        await provider.update_story("PROJ-2", StoryUpdate(clear_points=True))
    assert not respx.calls


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("account_id", ["557058:abc", None])
async def test_assignment_payload_and_empty_response(provider, account_id):
    route = respx.put(f"{BASE}/issue/PROJ-2/assignee").respond(204)
    await provider.assign_issue("PROJ-2", account_id)
    assert json.loads(route.calls.last.request.content) == {"accountId": account_id}


@pytest.mark.asyncio
@respx.mock
async def test_current_user_reads_myself(provider):
    respx.get(f"{BASE}/myself").respond(200, json={"accountId": "557058:abc"})
    assert await provider.current_account_id() == "557058:abc"


@pytest.mark.asyncio
@respx.mock
async def test_transition_metadata_and_post_payload(provider):
    route = respx.get(f"{BASE}/issue/PROJ-2/transitions").respond(
        200,
        json={
            "transitions": [
                {
                    "id": "31",
                    "name": "Finish",
                    "to": {"name": "Done"},
                    "fields": {
                        "resolution": {"required": True},
                        "summary": {"required": False},
                    },
                }
            ]
        },
    )
    transitions = await provider.list_transitions("PROJ-2")
    assert route.calls.last.request.url.params["expand"] == "transitions.fields"
    assert transitions[0].required_fields == ("resolution",)
    write = respx.post(f"{BASE}/issue/PROJ-2/transitions").respond(204)
    await provider.transition_issue("PROJ-2", "31")
    assert json.loads(write.calls.last.request.content) == {"transition": {"id": "31"}}


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize("outcome", ["timeout", 408, 500, 503])
async def test_uncertain_mutations_are_never_retried(provider, outcome):
    provider = JiraProvider(replace(provider.settings, max_retries=3))
    route = respx.post(f"{BASE}/issue/PROJ-2/transitions")
    if outcome == "timeout":
        route.mock(side_effect=httpx.ReadTimeout("timeout"))
    else:
        route.respond(outcome, json={"errorMessages": ["Unavailable"]})
    with pytest.raises(UncertainWriteException, match="Inspect the issue"):
        await provider.transition_issue("PROJ-2", "31")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_jira_rejection_preserves_field_detail(provider):
    route = respx.post(f"{BASE}/issue/PROJ-2/transitions").respond(
        400,
        json={"errors": {"resolution": "Required"}},
    )
    with pytest.raises(httpx.HTTPStatusError, match="resolution: Required"):
        await provider.transition_issue("PROJ-2", "31")
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_search_collects_every_page_with_same_jql(provider):
    route = respx.post(f"{BASE}/search/jql").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "issues": [
                        {
                            "key": "OTHER-1",
                            "fields": {
                                "summary": "Bug",
                                "issuetype": {"name": "Bug"},
                                "status": {"name": "Done"},
                            },
                        }
                    ],
                    "isLast": False,
                    "nextPageToken": "next",
                },
            ),
            httpx.Response(
                200, json={"issues": [{"key": "PROJ-2", "fields": {}}], "isLast": True}
            ),
        ]
    )
    items = await provider.search_work_items("project IN (PROJ, OTHER)")
    assert [item.key for item in items] == ["OTHER-1", "PROJ-2"]
    assert items[0].status == "Done" and items[1].issue_type == "Unknown"
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert all(body["jql"] == "project IN (PROJ, OTHER)" for body in bodies)
    assert bodies[1]["nextPageToken"] == "next"


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "body,error",
    [
        ({"issues": [], "isLast": False}, "omitted"),
        ({"issues": [], "isLast": False, "nextPageToken": "same"}, "repeated"),
    ],
)
async def test_bad_pagination_stops_instead_of_looping(provider, body, error):
    route = respx.post(f"{BASE}/search/jql").respond(200, json=body)
    with pytest.raises(ValueError, match=error):
        await provider.search_work_items("project = PROJ")
    assert route.call_count <= 2


@pytest.mark.asyncio
@respx.mock
async def test_new_mutations_reject_unsafe_issue_keys_before_http(provider):
    for operation in (
        lambda: provider.update_story("../myself", StoryUpdate(title="Title")),
        lambda: provider.assign_issue("PROJ-2/other", "abc"),
        lambda: provider.transition_issue("PROJ-2?x=y", "31"),
    ):
        with pytest.raises(ValueError, match="issue key"):
            await operation()
    assert not respx.calls
