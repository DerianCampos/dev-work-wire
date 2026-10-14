import re
from typing import Optional

import backoff
import httpx

from devworkwire.core.domain import Epic, IssueId, Label, Priority, StoryPoints, UserStory
from devworkwire.core.domain.exceptions import UncertainWriteException
from devworkwire.core.ports.work_item_changes import (
    StoryUpdate, TransitionSummary, validate_account_id, validate_issue_key,
)
from devworkwire.core.ports.jira_config_store import PROJECT_KEY_RE
from devworkwire.core.ports.work_item_provider import WorkItemProvider, WorkItemSummary
from devworkwire.infrastructure.external.jira.markdown_to_adf import markdown_to_adf
from devworkwire.infrastructure.external.jira.settings import JiraSettings


def _is_transient_error(error: Exception) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 429, 500, 502, 503, 504}
    return isinstance(error, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout))


def _adf_text(node: dict) -> str:
    """Concatenated text of an ADF ``text`` node and any nested text nodes."""
    if node.get("type") == "text":
        return node.get("text", "")
    return "".join(_adf_text(child) for child in node.get("content", []))


def _adf_to_text(doc: dict) -> str:
    """Renders an Atlassian Document Format doc as plain, readable text.

    Handles the node types Jira commonly uses in Epic descriptions
    (paragraphs and bullet/ordered lists); unsupported node types are
    skipped rather than raising.
    """
    blocks = []
    for node in doc.get("content", []):
        node_type = node.get("type")
        if node_type == "paragraph":
            blocks.append(_adf_text(node))
        elif node_type in ("bulletList", "orderedList"):
            items = [
                f"- {_adf_text(item)}" for item in node.get("content", [])
            ]
            blocks.append("\n".join(items))
    return "\n\n".join(block for block in blocks if block)


def _raise_for_status_with_jira_detail(response: httpx.Response) -> None:
    """Like ``response.raise_for_status()``, but folds Jira's error body
    (``errorMessages``/``errors``) into the exception message — the bare
    status code alone isn't actionable for a 400 field-validation failure.
    """
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        detail = None
        try:
            body = response.json()
            parts = list(body.get("errorMessages", []))
            parts += [f"{field}: {msg}" for field, msg in body.get("errors", {}).items()]
            detail = "; ".join(parts) if parts else None
        except ValueError:
            detail = response.text or None
        if detail:
            raise httpx.HTTPStatusError(
                f"{error}. Jira response: {detail}", request=error.request, response=error.response
            ) from error
        raise


_MOSCOW_TO_JIRA_PRIORITY = {
    "Must Have": "Highest",
    "Should Have": "High",
    "Could Have": "Medium",
    "Won't Have": "Low",
}
_ISSUE_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*-\d+\Z")
_ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9:_-]+\Z")
_PROJECT_KEY_RE = PROJECT_KEY_RE


def _jira_priority_name(priority: Priority) -> str:
    """Maps MoSCoW priority wording (used in epic.md/stories.md) to Jira
    Cloud's default priority scheme. A priority name that isn't a MoSCoW
    term (e.g. already 'High') passes through unchanged, so this doesn't
    break projects/files that use Jira-native priority names directly.
    """
    return _MOSCOW_TO_JIRA_PRIORITY.get(priority.name, priority.name)


class JiraProvider(WorkItemProvider):
    def __init__(self, settings: JiraSettings):
        self.settings = settings
        retry = backoff.on_exception(
            backoff.expo,
            (httpx.RequestError, httpx.HTTPStatusError),
            max_tries=settings.max_retries,
            giveup=lambda e: not _is_transient_error(e),
        )
        self._fetch_epic_with_retry = retry(self._fetch_epic)
        self._fetch_story_with_retry = retry(self._fetch_story)
        self._list_stories_with_retry = retry(self._list_stories)
        self._list_assigned_with_retry = retry(self._list_assigned_work_items)
        self._current_account_with_retry = retry(self._current_account_id)
        self._transitions_with_retry = retry(self._list_transitions)
        self._search_with_retry = retry(self._search_work_items)
        self._issue_type_with_retry = retry(self._get_issue_type)

    @property
    def _issue_url(self) -> str:
        return f"{self.settings.base_url}/rest/api/{self.settings.api_version}/issue"

    async def fetch_epic(self, key: str) -> Optional[Epic]:
        return await self._fetch_epic_with_retry(key)

    async def update_story(self, key: str, update: StoryUpdate) -> None:
        validate_issue_key(key)
        fields: dict[str, object] = {}
        if update.title is not None:
            fields["summary"] = update.title
        if update.description is not None:
            fields["description"] = markdown_to_adf(update.description) if update.description else None
        if update.priority is not None:
            fields["priority"] = {"name": _jira_priority_name(Priority(update.priority))}
        if update.labels is not None:
            fields["labels"] = list(update.labels)
        if update.points is not None or update.clear_points:
            if not self.settings.story_points_field:
                raise ValueError("Story points field is not configured")
            fields[self.settings.story_points_field] = update.points
        if update.epic_key is not None:
            fields["parent"] = {"key": update.epic_key}
        payload: dict[str, object] = {"fields": fields}
        if update.clear_epic:
            payload["update"] = {"parent": [{"set": {"none": True}}]}
        await self._write("PUT", f"{self._issue_url}/{key}", payload)

    async def get_issue_type(self, key: str) -> str | None:
        validate_issue_key(key)
        return await self._issue_type_with_retry(key)

    async def _get_issue_type(self, key: str) -> str | None:
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.get(
                f"{self._issue_url}/{key}", params={"fields": "issuetype"},
                auth=(self.settings.username, self.settings.api_token),
            )
            if response.status_code == 404:
                return None
            _raise_for_status_with_jira_detail(response)
            return (response.json().get("fields", {}).get("issuetype") or {}).get("name", "Unknown")

    async def current_account_id(self) -> str:
        return await self._current_account_with_retry()

    async def _current_account_id(self) -> str:
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.get(
                f"{self.settings.base_url}/rest/api/{self.settings.api_version}/myself",
                auth=(self.settings.username, self.settings.api_token),
            )
            _raise_for_status_with_jira_detail(response)
            account_id = response.json()["accountId"]
            validate_account_id(account_id)
            return account_id

    async def assign_issue(self, key: str, account_id: str | None) -> None:
        validate_issue_key(key)
        if account_id is not None:
            validate_account_id(account_id)
        await self._write("PUT", f"{self._issue_url}/{key}/assignee", {"accountId": account_id})

    async def list_transitions(self, key: str) -> list[TransitionSummary]:
        validate_issue_key(key)
        return await self._transitions_with_retry(key)

    async def _list_transitions(self, key: str) -> list[TransitionSummary]:
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.get(
                f"{self._issue_url}/{key}/transitions",
                params={"expand": "transitions.fields"},
                auth=(self.settings.username, self.settings.api_token),
            )
            _raise_for_status_with_jira_detail(response)
            return [TransitionSummary(
                id=transition["id"], name=transition["name"], status=transition["to"]["name"],
                required_fields=tuple(
                    field for field, metadata in transition.get("fields", {}).items()
                    if metadata.get("required")
                ),
            ) for transition in response.json().get("transitions", [])]

    async def transition_issue(self, key: str, transition_id: str) -> None:
        validate_issue_key(key)
        if not transition_id.isdecimal():
            raise ValueError("Transition ID must be numeric")
        await self._write(
            "POST", f"{self._issue_url}/{key}/transitions", {"transition": {"id": transition_id}},
        )

    async def _write(self, method: str, url: str, payload: dict[str, object]) -> None:
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                response = await client.request(
                    method, url, json=payload, auth=(self.settings.username, self.settings.api_token),
                )
                _raise_for_status_with_jira_detail(response)
        except httpx.RequestError as error:
            raise UncertainWriteException(
                "Write outcome is uncertain. Inspect the issue in Jira before retrying."
            ) from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 408 or error.response.status_code >= 500:
                raise UncertainWriteException(
                    "Write outcome is uncertain. Inspect the issue in Jira before retrying."
                ) from error
            raise

    async def search_work_items(self, jql: str) -> list[WorkItemSummary]:
        if not jql.strip():
            raise ValueError("JQL cannot be empty")
        return await self._search_with_retry(jql)

    async def _search_work_items(self, jql: str) -> list[WorkItemSummary]:
        issues = await self._search_issues(jql, ["summary", "issuetype", "status"])
        return self._work_item_summaries(issues)

    async def create_epic(self, epic: Epic) -> str:
        # Jira creation is not idempotent; retrying an uncertain response can duplicate issues.
        return await self._create_epic(epic)

    async def create_story(self, story: UserStory, epic_key: str) -> str:
        return await self._create_story(story, epic_key)

    async def fetch_story(self, key: str) -> Optional[UserStory]:
        return await self._fetch_story_with_retry(key)

    async def list_stories(self, epic_key: str) -> list[UserStory]:
        if not _ISSUE_KEY_RE.fullmatch(epic_key):
            raise ValueError(f"Invalid Jira issue key: {epic_key}")
        return await self._list_stories_with_retry(epic_key)

    async def list_assigned_work_items(
        self, account_id: str | None = None
    ) -> list[WorkItemSummary]:
        if account_id is not None and not _ACCOUNT_ID_RE.fullmatch(account_id):
            raise ValueError("Invalid Jira account ID")
        if not _PROJECT_KEY_RE.fullmatch(self.settings.project_key):
            raise ValueError("Invalid Jira project key")
        return await self._list_assigned_with_retry(account_id)

    async def _fetch_issue(self, key: str) -> Optional[dict]:
        auth = (self.settings.username, self.settings.api_token)
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.get(f"{self._issue_url}/{key}", auth=auth)
            if response.status_code == 404:
                return None
            _raise_for_status_with_jira_detail(response)
            return response.json()

    @staticmethod
    def _require_issue_type(data: dict, expected: str) -> None:
        issue_type = (data.get("fields", {}).get("issuetype") or {}).get("name")
        if issue_type != expected:
            article = "an" if expected == "Epic" else "a"
            raise ValueError(f"Issue {data['key']} is not {article} {expected}")

    def _story_from_issue(self, data: dict, include_points: bool = True) -> UserStory:
        self._require_issue_type(data, "Story")
        fields = data.get("fields", {})
        description_field = fields.get("description")
        description = (
            _adf_to_text(description_field)
            if isinstance(description_field, dict)
            else str(description_field or "")
        )
        priority_name = (fields.get("priority") or {}).get("name")
        points_value = (
            fields.get(self.settings.story_points_field)
            if include_points and self.settings.story_points_field else None
        )
        if points_value is not None:
            if isinstance(points_value, bool) or not isinstance(points_value, (int, float)) or int(points_value) != points_value:
                raise ValueError(f"Issue {data['key']} has non-integer story points")
            story_points = StoryPoints(value=int(points_value))
        else:
            story_points = None
        return UserStory(
            title=fields.get("summary", ""),
            description=description,
            issue_id=IssueId(key=data["key"]),
            priority=Priority.from_jira_name(priority_name) if priority_name else None,
            story_points=story_points,
            labels=[Label(name=label) for label in fields.get("labels") or []],
            epic_key=(fields.get("parent") or {}).get("key"),
            status=(fields.get("status") or {}).get("name"),
        )

    async def _fetch_epic(self, key: str) -> Optional[Epic]:
        data = await self._fetch_issue(key)
        if data is None:
            return None
        self._require_issue_type(data, "Epic")
        fields = data.get("fields", {})
        description_field = fields.get("description")
        description = (
            _adf_to_text(description_field)
            if isinstance(description_field, dict)
            else str(description_field or "")
        )
        priority_name = (fields.get("priority") or {}).get("name")
        epic = Epic.create(
            title=fields.get("summary", ""),
            description=description,
            priority=Priority.from_jira_name(priority_name) if priority_name else None,
            labels=[Label(name=label) for label in fields.get("labels") or []],
        )
        epic.issue_id = IssueId(key=data["key"])
        return epic

    async def _fetch_story(self, key: str) -> Optional[UserStory]:
        data = await self._fetch_issue(key)
        return self._story_from_issue(data) if data is not None else None

    async def _list_stories(self, epic_key: str) -> list[UserStory]:
        issues = await self._search_issues(
            f'parent = "{epic_key}" AND issuetype = Story ORDER BY key ASC',
            ["summary", "issuetype", "status"],
        )
        return [self._story_from_issue(issue, include_points=False) for issue in issues]

    async def _list_assigned_work_items(
        self, account_id: str | None
    ) -> list[WorkItemSummary]:
        assignee = f'"{account_id}"' if account_id is not None else "currentUser()"
        jql = (
            f'project = "{self.settings.project_key}" AND assignee = {assignee} '
            "AND statusCategory != Done ORDER BY updated DESC, key ASC"
        )
        issues = await self._search_issues(jql, ["summary", "issuetype", "status"])
        return self._work_item_summaries(issues)

    @staticmethod
    def _work_item_summaries(issues: list[dict]) -> list[WorkItemSummary]:
        return [
            WorkItemSummary(
                key=issue["key"],
                issue_type=(issue.get("fields", {}).get("issuetype") or {}).get("name") or "Unknown",
                title=issue.get("fields", {}).get("summary") or "",
                status=(issue.get("fields", {}).get("status") or {}).get("name") or "Unknown",
            )
            for issue in issues
        ]

    async def _search_issues(self, jql: str, fields: list[str]) -> list[dict]:
        issues: list[dict] = []
        next_page_token = None
        seen_tokens: set[str] = set()
        auth = (self.settings.username, self.settings.api_token)
        search_url = f"{self.settings.base_url}/rest/api/3/search/jql"
        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            while True:
                payload: dict[str, object] = {
                    "jql": jql,
                    "fields": fields,
                    "maxResults": 100,
                }
                if next_page_token:
                    payload["nextPageToken"] = next_page_token
                response = await client.post(search_url, json=payload, auth=auth)
                _raise_for_status_with_jira_detail(response)
                data = response.json()
                issues.extend(data.get("issues", []))
                if data.get("isLast"):
                    return issues
                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    raise ValueError("Jira search response omitted the next page token")
                if next_page_token in seen_tokens:
                    raise ValueError("Jira search response repeated a page token")
                seen_tokens.add(next_page_token)

    async def _create_epic(self, epic: Epic) -> str:
        auth = (self.settings.username, self.settings.api_token)

        payload: dict[str, dict[str, object]] = {
            "fields": {
                "project": {"key": self.settings.project_key},
                "summary": epic.title,
                "description": markdown_to_adf(epic.description),
                "issuetype": {"name": "Epic"},
            }
        }

        if epic.priority:
            payload["fields"]["priority"] = {"name": _jira_priority_name(epic.priority)}

        if epic.labels:
            payload["fields"]["labels"] = [lbl.name for lbl in epic.labels]

        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(self._issue_url, json=payload, auth=auth)
            _raise_for_status_with_jira_detail(response)
            data = response.json()
            return data["key"]

    async def _create_story(self, story: UserStory, epic_key: str) -> str:
        auth = (self.settings.username, self.settings.api_token)

        payload: dict[str, dict[str, object]] = {
            "fields": {
                "project": {"key": self.settings.project_key},
                "summary": story.title,
                "description": markdown_to_adf(story.description),
                "issuetype": {"name": "Story"},
                "parent": {"key": epic_key},
            }
        }

        if story.priority:
            payload["fields"]["priority"] = {"name": _jira_priority_name(story.priority)}

        if story.labels:
            payload["fields"]["labels"] = [lbl.name for lbl in story.labels]

        if story.story_points is not None:
            if not self.settings.story_points_field:
                raise ValueError("Story points field is not configured")
            payload["fields"][self.settings.story_points_field] = story.story_points.value

        async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
            response = await client.post(self._issue_url, json=payload, auth=auth)
            _raise_for_status_with_jira_detail(response)
            data = response.json()
            return data["key"]
