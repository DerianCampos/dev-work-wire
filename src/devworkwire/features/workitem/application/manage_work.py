from devworkwire.core.domain.exceptions import NotFoundException
from devworkwire.core.ports.jira_config_store import normalize_project_key
from devworkwire.core.ports.work_item_changes import (
    SearchFilters,
    StoryUpdate,
    validate_account_id,
    validate_issue_key,
)
from devworkwire.core.ports.work_item_provider import WorkItemProvider


async def update_story(
    provider: WorkItemProvider, key: str, update: StoryUpdate
) -> None:
    validate_issue_key(key)
    await _require_type(provider, key, "Story")
    if update.epic_key:
        await _require_type(provider, update.epic_key, "Epic")
    await provider.update_story(key, update)


async def _require_type(provider: WorkItemProvider, key: str, expected: str) -> None:
    issue_type = await provider.get_issue_type(key)
    if issue_type is None:
        raise NotFoundException(f"{expected} {key} not found")
    if issue_type != expected:
        raise ValueError(
            f"Issue {key} is not {'an' if expected == 'Epic' else 'a'} {expected}"
        )


async def assign_issue(
    provider: WorkItemProvider, key: str, assignee: str | None
) -> str | None:
    validate_issue_key(key)
    if assignee == "me":
        assignee = await provider.current_account_id()
    if assignee is not None:
        validate_account_id(assignee)
    await provider.assign_issue(key, assignee)
    return assignee


def _literal(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_jql(filters: SearchFilters, project_key: str) -> str:
    if filters.jql is not None:
        return filters.jql
    clauses = [f"project = {_literal(normalize_project_key(project_key))}"]
    for field, values in (
        ("status", filters.statuses),
        ("issuetype", filters.issue_types),
    ):
        if values:
            clauses.append(
                f"{field} IN ({', '.join(_literal(value) for value in values)})"
            )
    clauses.extend(f"labels = {_literal(label)}" for label in filters.labels)
    return " AND ".join(clauses) + " ORDER BY updated DESC, key ASC"
