from collections.abc import Awaitable, Callable
from dataclasses import asdict

import httpx
import typer

from devworkwire.core.composition import Container
from devworkwire.core.domain.exceptions import (
    BusinessRuleViolation,
    InvalidTransitionException,
    NotFoundException,
    UncertainWriteException,
)
from devworkwire.core.ports.work_item_changes import (
    SearchFilters,
    StoryUpdate,
    TransitionSummary,
    validate_issue_key,
)
from devworkwire.features.progress.application.transitions import (
    ProgressActions,
    TransitionRequest,
)
from devworkwire.features.workitem.application import manage_work


class WorkActions:
    def __init__(self, container: Container, record: Callable[[str, dict, str], None]):
        self.container = container
        self.record = record

    async def _run(self, action: str, operation: Callable[[], Awaitable[dict]]) -> bool:
        try:
            data = await operation()
        except Exception as error:
            code = "COMMAND_FAILED"
            if isinstance(error, UncertainWriteException):
                code = "WRITE_UNCERTAIN"
            elif isinstance(error, NotFoundException):
                code = "NOT_FOUND"
            elif isinstance(error, InvalidTransitionException):
                code = "INVALID_TRANSITION"
            elif isinstance(error, (ValueError, BusinessRuleViolation)):
                code = "VALIDATION_FAILED"
            elif isinstance(error, httpx.HTTPStatusError):
                code = {
                    401: "AUTHENTICATION_FAILED",
                    403: "PERMISSION_DENIED",
                    404: "NOT_FOUND",
                }.get(
                    error.response.status_code,
                    "JIRA_ERROR",
                )
            self.record("error", {}, code)
            message = " ".join(str(error).splitlines())
            typer.echo(f"Error {action}: {message}", err=True)
            return False
        self.record("completed", data, "COMMAND_FAILED")
        self._show(data)
        return True

    @staticmethod
    def _show(data: dict) -> None:
        if "items" in data:
            if not data["items"]:
                typer.echo("No matching work items.")
            for item in data["items"]:
                typer.echo(
                    f"{item['key']}  {item['issue_type']}  {item['title']}  [{item['status']}]"
                )
        elif "transitions" in data:
            if not data["transitions"]:
                typer.echo("No transitions available.")
            for transition in data["transitions"]:
                required = transition["required_fields"]
                detail = (
                    f" (required fields: {', '.join(required)})" if required else ""
                )
                typer.echo(
                    f"{transition['id']}  {transition['name']} → {transition['status']}{detail}"
                )
        elif "transition" in data:
            typer.echo(
                f"Transition accepted for {data['key']}: {data['transition']['status']}"
            )
        elif "assignee" in data:
            typer.echo(
                f"Assignment accepted for {data['key']}: {data['assignee'] or 'unassigned'}"
            )
        else:
            typer.echo(
                f"Update accepted for {data['key']}: {', '.join(data['changed_fields'])}"
            )

    async def update(self, key: str, update: StoryUpdate) -> bool:
        async def operation() -> dict:
            await manage_work.update_story(
                self.container.get_jira_provider(), key, update
            )
            changes = [
                name
                for name in (
                    "title",
                    "description",
                    "priority",
                    "labels",
                    "points",
                    "epic_key",
                )
                if getattr(update, name) is not None
            ]
            if update.clear_points:
                changes.append("points")
            if update.clear_epic:
                changes.append("epic_key")
            return {"key": key, "changed_fields": changes}

        return await self._run("updating story", operation)

    async def assign(
        self, key: str, assignee: str | None, unassign: bool = False
    ) -> bool:
        async def operation() -> dict:
            if (assignee is None and not unassign) or (
                assignee is not None and unassign
            ):
                raise ValueError("Choose exactly one of --assignee or --unassign")
            assigned = await manage_work.assign_issue(
                self.container.get_jira_provider(), key, assignee
            )
            return {"key": key, "assignee": assigned}

        return await self._run("assigning issue", operation)

    async def transitions(self, key: str) -> bool:
        async def operation() -> dict:
            transitions = await self.available_transitions(key)
            return {
                "key": key,
                "transitions": [asdict(transition) for transition in transitions],
            }

        return await self._run("listing transitions", operation)

    async def available_transitions(self, key: str) -> list[TransitionSummary]:
        validate_issue_key(key)
        return await self.container.get_jira_provider().list_transitions(key)

    async def transition(self, key: str, request: TransitionRequest) -> bool:
        async def operation() -> dict:
            _, project_config = self.container.resolve_jira_connection()
            progress = ProgressActions(
                self.container.get_jira_provider(),
                project_config.transition_overrides if project_config else {},
            )
            selected = await progress.change_status(key, request)
            return {"key": key, "transition": asdict(selected)}

        return await self._run("transitioning issue", operation)

    async def search(self, filters: SearchFilters) -> bool:
        async def operation() -> dict:
            provider = self.container.get_jira_provider()
            jql = manage_work.search_jql(filters, provider.settings.project_key)
            items = await provider.search_work_items(jql)
            return {"jql": jql, "items": [asdict(item) for item in items]}

        return await self._run("searching issues", operation)
