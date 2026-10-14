import asyncio
import click
import io
import json
import re
import sys
from contextlib import redirect_stderr, redirect_stdout
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import httpx
import typer
from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from typer.core import TyperGroup

from devworkwire.config.jira_connection import ResolvedValue
from devworkwire.core.composition import Container
from devworkwire.core.domain import Epic, Label, Priority, StoryPoints, UserStory
from devworkwire.core.domain.exceptions import BusinessRuleViolation
from devworkwire.core.ports.jira_config_store import JiraCredentials, normalize_project_key
from devworkwire.core.ports.work_item_changes import SearchFilters, StoryUpdate
from devworkwire.features.progress.application.transitions import TransitionRequest
from devworkwire.features.import_.application.folder_preview import (
    FolderPreview,
    preview_folder as load_folder_preview,
)
from devworkwire.features.import_.application.folder_state import (
    ImportState,
    ItemRecord,
    lock_folder,
)
from devworkwire.presentation.cli.epic_panel import render_epic_panel, render_story_panel
from devworkwire.presentation.cli.menu_view import MenuGroup, MenuOption, run_menu
from devworkwire.presentation.cli.work_actions import WorkActions
from devworkwire.presentation.cli.work_menu import run_search_menu, run_work_menu

# Newer Typer releases bundle their own click copy, so their argument errors
# are not instances of the installed click package's ClickException.
try:
    from typer._click.exceptions import ClickException as _TyperClickException
    _CLICK_ERRORS: tuple[type[Exception], ...] = (click.ClickException, _TyperClickException)
except ImportError:
    _CLICK_ERRORS = (click.ClickException,)


class JsonTyperGroup(TyperGroup):
    def main(self, args=None, prog_name=None, standalone_mode=True, **extra):
        arguments = list(args) if args is not None else sys.argv[1:]
        json_requested = any(
            argument == "--format=json" or
            argument == "--format" and index + 1 < len(arguments) and arguments[index + 1] == "json"
            for index, argument in enumerate(arguments)
        )
        if not json_requested:
            return super().main(
                args=args, prog_name=prog_name, standalone_mode=standalone_mode, **extra
            )
        try:
            outcome = super().main(
                args=args, prog_name=prog_name, standalone_mode=False, **extra
            )
        except _CLICK_ERRORS as error:
            command = next(
                (argument for argument in arguments if argument in self.commands), None
            )
            typer.echo(json.dumps({
                "command": command,
                "status": "error",
                "data": {},
                "error": {"code": "INVALID_ARGUMENT", "message": error.format_message()},
            }))
            outcome = error.exit_code
        if standalone_mode:
            raise SystemExit(outcome if isinstance(outcome, int) else 0)
        return outcome


app = typer.Typer(cls=JsonTyperGroup)
config_app = typer.Typer(help="Manage the stored Jira connection and projects.")
app.add_typer(config_app, name="config")
container = Container()


class OutputFormat(str, Enum):
    text = "text"
    json = "json"


@dataclass
class CommandResult:
    status: str = "completed"
    data: dict | None = None
    error_code: str = "COMMAND_FAILED"


_output_format: ContextVar[OutputFormat] = ContextVar("output_format", default=OutputFormat.text)
_command_result: ContextVar[CommandResult | None] = ContextVar("command_result", default=None)


def _record(status: str, data: dict, error_code: str = "COMMAND_FAILED") -> None:
    result = _command_result.get()
    if result is not None:
        result.status = status
        result.data = data
        result.error_code = error_code


work_actions = WorkActions(container, _record)


def _created(item_type: str, key: str, story_id: str | None = None) -> None:
    result = _command_result.get()
    if result is not None:
        if result.data is None:
            result.data = {}
        item = {"type": item_type, "key": key}
        if story_id is not None:
            item["story_id"] = story_id
        result.data.setdefault("created", []).append(item)


def _run_command(command: str, action: Callable[[], bool]) -> None:
    if _output_format.get() == OutputFormat.text:
        if not action():
            raise typer.Exit(code=1)
        return

    result = CommandResult()
    token = _command_result.set(result)
    standard_output = io.StringIO()
    standard_error = io.StringIO()
    try:
        with redirect_stdout(standard_output), redirect_stderr(standard_error):
            succeeded = action()
    finally:
        _command_result.reset(token)
    if standard_error.getvalue():
        sys.stderr.write(standard_error.getvalue())
    if not succeeded:
        result.status = "partial" if result.data and result.data.get("created") else "error"
    messages = [line.strip() for line in standard_error.getvalue().splitlines() if line.strip()]
    if not messages:
        messages = [line.strip() for line in standard_output.getvalue().splitlines() if line.strip()]
    message = next(
        (line for line in messages if line.startswith(("Error ", "Use --yes", "Choose exactly"))),
        messages[0] if messages else "Command failed",
    )
    payload = {
        "command": command,
        "status": result.status,
        "data": result.data or {},
        "error": None if succeeded else {
            "code": result.error_code,
            "message": message,
        },
    }
    typer.echo(json.dumps(payload, ensure_ascii=False))
    if not succeeded:
        raise typer.Exit(code=1)


_FETCH_EPIC = "Retrieve epic by key"
_FETCH_STORY = "Retrieve story by key"
_LIST_STORIES = "List stories in epic"
_LIST_ASSIGNED = "List assigned open work"
_CREATE_EPIC = "Create epic"
_CREATE_STORY = "Create story in epic"
_PREVIEW_FOLDER = "Preview and validate folder"
_CONFIGURE = "Configure connection and projects"
_IMPORT_FOLDER = "Import epic and stories from folder"
_EXIT = "Exit"
_SEARCH = "Search work items"
_MANAGE = "Manage work items"

_MENU_GROUPS = (
    MenuGroup(
        "WORK ITEMS", "Browse existing\nwork items", "◆", "#27baff",
        (
            MenuOption("1", _FETCH_EPIC, _FETCH_EPIC),
            MenuOption("2", _FETCH_STORY, _FETCH_STORY),
            MenuOption("3", _LIST_STORIES, _LIST_STORIES),
            MenuOption("4", _LIST_ASSIGNED, _LIST_ASSIGNED),
            MenuOption("s", _SEARCH, _SEARCH),
        ),
    ),
    MenuGroup(
        "CREATE", "Add individual\nwork items", "+", "#00e587",
        (
            MenuOption("5", _CREATE_EPIC, _CREATE_EPIC),
            MenuOption("6", _CREATE_STORY, _CREATE_STORY),
            MenuOption("w", _MANAGE, _MANAGE),
        ),
    ),
    MenuGroup(
        "IMPORT", "Load work items\nfrom a folder", "↑", "#ffae00",
        (MenuOption("7", _IMPORT_FOLDER, _IMPORT_FOLDER),),
    ),
    MenuGroup(
        "LOCAL", "Check files and\nmanage settings", "▣", "#b05bff",
        (
            MenuOption("8", _PREVIEW_FOLDER, _PREVIEW_FOLDER),
            MenuOption("9", _CONFIGURE, _CONFIGURE),
        ),
    ),
)


@dataclass(frozen=True)
class WorkItemInput:
    title: str
    description: str = ""
    priority: str | None = None
    labels: tuple[str, ...] = ()
    points: int | None = None


def _priority(value: str | None) -> Priority | None:
    return Priority.from_jira_name(value.strip()) if value and value.strip() else None


def _labels(values: tuple[str, ...]) -> list[Label]:
    return [Label(name=value.strip()) for value in values]


def _epic_data(epic: Epic) -> dict:
    return {
        "key": epic.issue_id.key if epic.issue_id else None,
        "title": epic.title,
        "description": epic.description,
        "priority": epic.priority.name if epic.priority else None,
        "labels": [label.name for label in epic.labels],
    }


def _story_data(story: UserStory) -> dict:
    return {
        "key": story.issue_id.key if story.issue_id else None,
        "title": story.title,
        "description": story.description,
        "priority": story.priority.name if story.priority else None,
        "labels": [label.name for label in story.labels],
        "points": story.story_points.value if story.story_points else None,
        "epic_key": story.epic_key,
        "status": story.status,
    }


def _preview_data(preview: FolderPreview, state: ImportState | None) -> dict:
    stories = []
    for story in preview.stories:
        record = state.stories.get(story.story_id) if state else None
        stories.append({
            "id": story.story_id,
            "title": story.title,
            "line": story.line,
            "action": "create" if record is None else "unresolved" if record.status == "pending" else "skip",
            "key": record.key if record else None,
            "changed": record.source_hash != story.source_hash if record else False,
            "item": _story_data(story.item) if story.item else None,
        })
    epic_record = state.epic if state else None
    return {
        "epic": {
            "title": preview.epic_title,
            "action": "create" if epic_record is None else "unresolved" if epic_record.status == "pending" else "skip",
            "key": epic_record.key if epic_record else None,
            "changed": epic_record.source_hash != preview.epic_hash if epic_record else False,
            "item": _epic_data(preview.epic) if preview.epic else None,
        },
        "stories": stories,
        "missing_story_ids": sorted(_missing_story_ids(preview, state)) if state else [],
        "errors": [{"file": error.file, "line": error.line, "message": error.message} for error in preview.errors],
    }


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--format"),
    project: Optional[str] = typer.Option(
        None, "--project", help="Jira project key for this run (default: the stored default project)"
    ),
) -> None:
    """DevWorkWire CLI. Run without a subcommand for the interactive menu."""
    _output_format.set(output_format)
    container.select_project(project)
    if ctx.invoked_subcommand is None:
        if output_format == OutputFormat.json:
            raise typer.BadParameter("JSON output requires a direct command", param_hint="--format")
        _run_interactive_menu()


@app.command("fetch-epic")
def fetch_epic(key: str) -> None:
    _run_command("fetch-epic", lambda: asyncio.run(_fetch_epic(key)))


@app.command("fetch-story")
def fetch_story(key: str) -> None:
    _run_command("fetch-story", lambda: asyncio.run(_fetch_story(key)))


@app.command("list-stories")
def list_stories(epic_key: str) -> None:
    _run_command("list-stories", lambda: asyncio.run(_list_stories(epic_key)))


@app.command("list-assigned")
def list_assigned(
    assignee: Optional[str] = typer.Option(None, "--assignee")
) -> None:
    """List open work assigned to the current user or a Jira account ID."""
    _run_command("list-assigned", lambda: asyncio.run(_list_assigned(assignee)))


@app.command("create-epic")
def create_epic(
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option("", "--description"),
    priority: Optional[str] = typer.Option(None, "--priority"),
    labels: list[str] = typer.Option([], "--label"),
) -> None:
    """Create one epic. Repeat --label to add multiple labels."""
    item = WorkItemInput(title, description, priority, tuple(labels))
    _run_command("create-epic", lambda: asyncio.run(_create_epic(item)))


@app.command("create-story")
def create_story(
    epic_key: str,
    title: str = typer.Option(..., "--title"),
    description: str = typer.Option("", "--description"),
    priority: Optional[str] = typer.Option(None, "--priority"),
    labels: list[str] = typer.Option([], "--label"),
    points: Optional[int] = typer.Option(None, "--points", min=0),
) -> None:
    """Create one story under an existing epic."""
    item = WorkItemInput(title, description, priority, tuple(labels), points)
    _run_command("create-story", lambda: asyncio.run(_create_story(epic_key, item)))


@app.command("update-story")
def update_story(
    key: str,
    title: Optional[str] = typer.Option(None, "--title"),
    description: Optional[str] = typer.Option(None, "--description"),
    priority: Optional[str] = typer.Option(None, "--priority"),
    labels: list[str] = typer.Option([], "--label"),
    points: Optional[int] = typer.Option(None, "--points", min=0),
    epic: Optional[str] = typer.Option(None, "--epic"),
    clear_labels: bool = typer.Option(False, "--clear-labels"),
    clear_points: bool = typer.Option(False, "--clear-points"),
    clear_epic: bool = typer.Option(False, "--clear-epic"),
) -> None:
    """Patch supplied story fields. --label replaces labels; omitted fields stay unchanged."""
    try:
        if clear_labels and labels:
            raise ValueError("Choose --label or --clear-labels")
        update = StoryUpdate(
            title=title, description=description, priority=priority,
            labels=tuple(labels) if labels or clear_labels else None,
            points=points, epic_key=epic, clear_points=clear_points, clear_epic=clear_epic,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _run_command("update-story", lambda: asyncio.run(work_actions.update(key, update)))


@app.command("assign")
def assign(
    key: str,
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Jira account ID or me"),
    unassign: bool = typer.Option(False, "--unassign"),
) -> None:
    """Assign any issue type, or remove its assignee."""
    _run_command("assign", lambda: asyncio.run(work_actions.assign(key, assignee, unassign)))


@app.command("list-transitions")
def list_transitions(key: str) -> None:
    """List available action IDs, names, and destination statuses."""
    _run_command("list-transitions", lambda: asyncio.run(work_actions.transitions(key)))


@app.command("transition")
def transition(
    key: str,
    status: Optional[str] = typer.Option(None, "--status"),
    transition_id: Optional[str] = typer.Option(None, "--transition-id"),
) -> None:
    """Change status using a destination status or an available transition ID."""
    try:
        request = TransitionRequest(status, transition_id)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _run_command("transition", lambda: asyncio.run(work_actions.transition(key, request)))


@app.command("search")
def search(
    statuses: list[str] = typer.Option([], "--status"),
    labels: list[str] = typer.Option([], "--label"),
    issue_types: list[str] = typer.Option([], "--type"),
    jql: Optional[str] = typer.Option(None, "--jql"),
) -> None:
    """Search the selected project with filters, or run raw JQL as written."""
    try:
        filters = SearchFilters(tuple(statuses), tuple(labels), tuple(issue_types), jql)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _run_command("search", lambda: asyncio.run(work_actions.search(filters)))


@app.command("import-folder")
def import_folder(
    folder_path: str,
    yes: bool = typer.Option(False, "--yes", help="Create without an interactive confirmation"),
) -> None:
    """Upload epic.md and optional stories.md from a folder."""
    _run_command("import-folder", lambda: asyncio.run(_import_folder(folder_path, yes=yes)))


@app.command("preview-folder")
def preview_folder(folder_path: str) -> None:
    """Show local work items and validation errors without contacting Jira."""
    _run_command("preview-folder", lambda: _preview_folder(folder_path))


@app.command("resolve-import")
def resolve_import(
    folder_path: str,
    item: str = typer.Option(..., "--item", help="epic or a story ID"),
    key: Optional[str] = typer.Option(None, "--key", help="Existing Jira issue key"),
    retry: bool = typer.Option(False, "--retry", help="Clear a confirmed absent attempt"),
) -> None:
    """Resolve an uncertain import after checking Jira."""
    def action() -> bool:
        if (key is None and not retry) or (key is not None and retry):
            typer.echo("Choose exactly one of --key or --retry.", err=True)
            return False
        return asyncio.run(_resolve_import(folder_path, item, key, retry))

    _run_command("resolve-import", action)


@app.command("rebind-import-story")
def rebind_import_story(folder_path: str, old_id: str, new_id: str) -> None:
    """Keep a recorded Jira key when a story heading ID changes."""
    _run_command("rebind-import-story", lambda: _reconcile_missing_story(folder_path, old_id, new_id))


@app.command("retire-import-story")
def retire_import_story(folder_path: str, story_id: str) -> None:
    """Remove a recorded story that is no longer in stories.md."""
    _run_command("retire-import-story", lambda: _reconcile_missing_story(folder_path, story_id, None))


@config_app.command("setup")
def config_setup(
    base_url: Optional[str] = typer.Option(None, "--base-url", help="e.g. https://your-domain.atlassian.net"),
    email: Optional[str] = typer.Option(None, "--email", help="Atlassian account email"),
    project_key: Optional[str] = typer.Option(None, "--project-key", help="Project to add (first one becomes the default)"),
) -> None:
    """Store the Jira connection (the API token is always prompted, never a flag)."""
    _run_command("config setup", lambda: _config_setup(base_url, email, project_key))


@config_app.command("add-project")
def config_add_project(
    project_key: str,
    make_default: bool = typer.Option(False, "--default", help="Also make it the default project"),
) -> None:
    """Add a Jira project you work in. The first project added becomes the default."""
    _run_command("config add-project", lambda: _config_projects("add", project_key, make_default))


@config_app.command("set-default")
def config_set_default(project_key: str) -> None:
    """Choose which configured project is used when --project is not given."""
    _run_command("config set-default", lambda: _config_projects("default", project_key))


@config_app.command("remove-project")
def config_remove_project(project_key: str) -> None:
    """Remove a project; if it was the default, the oldest remaining one takes over."""
    _run_command("config remove-project", lambda: _config_projects("remove", project_key))


@config_app.command("show")
def config_show() -> None:
    """Show the effective Jira settings, projects, and where each value comes from. The token is masked."""
    _run_command("config show", _config_show)


@config_app.command("clear")
def config_clear(
    yes: bool = typer.Option(False, "--yes", help="Clear without an interactive confirmation"),
) -> None:
    """Delete the stored connection and projects (environment variables are not touched)."""
    _run_command("config clear", lambda: _config_clear(yes))


async def _fetch_epic(key: str) -> bool:
    try:
        epic = await container.get_jira_provider().fetch_epic(key)
        if epic is None:
            typer.echo(f"Epic {key} not found.", err=True)
            _record("error", {"key": key}, "NOT_FOUND")
            return False
        _record("completed", {"epic": _epic_data(epic)})
        typer.secho(render_epic_panel(epic), fg=typer.colors.CYAN)
        return True
    except Exception as error:
        typer.echo(f"Error fetching epic: {error}", err=True)
        return False


async def _fetch_story(key: str) -> bool:
    try:
        story = await container.get_jira_provider().fetch_story(key)
        if story is None:
            typer.echo(f"Story {key} not found.", err=True)
            _record("error", {"key": key}, "NOT_FOUND")
            return False
        _record("completed", {"story": _story_data(story)})
        typer.secho(render_story_panel(story), fg=typer.colors.CYAN)
        return True
    except Exception as error:
        typer.echo(f"Error fetching story: {error}", err=True)
        return False


async def _list_stories(epic_key: str) -> bool:
    try:
        provider = container.get_jira_provider()
        if await provider.fetch_epic(epic_key) is None:
            typer.echo(f"Epic {epic_key} not found.", err=True)
            _record("error", {"epic_key": epic_key}, "NOT_FOUND")
            return False
        stories = await provider.list_stories(epic_key)
        _record("completed", {"epic_key": epic_key, "stories": [_story_data(story) for story in stories]})
        if not stories:
            typer.echo(f"No stories found in epic {epic_key}.")
            return True
        for story in stories:
            key = story.issue_id.key if story.issue_id else "Unknown"
            typer.echo(f"{key}  {story.title}  [{story.status or 'Unknown'}]")
        return True
    except Exception as error:
        typer.echo(f"Error listing stories: {error}", err=True)
        return False


async def _list_assigned(account_id: str | None) -> bool:
    try:
        work_items = await container.get_jira_provider().list_assigned_work_items(account_id)
        _record("completed", {"assignee": account_id or "me", "items": [
            {"key": item.key, "issue_type": item.issue_type, "title": item.title, "status": item.status}
            for item in work_items
        ]})
        if not work_items:
            typer.echo("No open work items assigned to this user in the configured project.")
            return True
        for work_item in work_items:
            typer.echo(
                f"{work_item.key}  {work_item.issue_type}  "
                f"{work_item.title}  [{work_item.status}]"
            )
        return True
    except Exception as error:
        typer.echo(f"Error listing assigned work: {error}", err=True)
        return False


async def _create_epic(item: WorkItemInput) -> bool:
    try:
        epic = Epic.create(
            title=item.title,
            description=item.description,
            priority=_priority(item.priority),
            labels=_labels(item.labels),
        )
        issue_key = await container.get_jira_provider().create_epic(epic)
        _record("completed", {"key": issue_key, "type": "epic"})
        typer.echo(f"Successfully created epic: {issue_key}")
        return True
    except Exception as error:
        typer.echo(f"Error creating epic: {error}", err=True)
        return False


async def _create_story(epic_key: str, item: WorkItemInput) -> bool:
    try:
        story = UserStory.create(
            title=item.title,
            description=item.description,
            priority=_priority(item.priority),
            story_points=StoryPoints(value=item.points) if item.points is not None else None,
            labels=_labels(item.labels),
        )
        provider = container.get_jira_provider()
        if await provider.fetch_epic(epic_key) is None:
            typer.echo(f"Epic {epic_key} not found.", err=True)
            _record("error", {"epic_key": epic_key}, "NOT_FOUND")
            return False
        issue_key = await provider.create_story(story, epic_key=epic_key)
        _record("completed", {"key": issue_key, "type": "story", "epic_key": epic_key})
        typer.echo(f"Successfully created story: {issue_key}")
        return True
    except Exception as error:
        typer.echo(f"Error creating story: {error}", err=True)
        return False


def _show_folder_preview(preview: FolderPreview, state: ImportState | None = None) -> None:
    _record("preview", _preview_data(preview, state), "VALIDATION_FAILED")
    typer.echo(f"Epic: {preview.epic_title or '(missing or invalid)'}")
    if state is not None:
        _show_item_status("epic", state.epic, preview.epic_hash)
    if preview.epic is not None:
        epic_details = []
        if preview.epic.priority:
            epic_details.append(f"priority={preview.epic.priority.name}")
        if preview.epic.labels:
            epic_details.append(f"labels={', '.join(label.name for label in preview.epic.labels)}")
        if epic_details:
            typer.echo(f"  {', '.join(epic_details)}")
    typer.echo(f"Stories ({len(preview.stories)}):")
    for story in preview.stories:
        details = []
        if story.item is not None:
            if story.item.priority:
                details.append(f"priority={story.item.priority.name}")
            if story.item.labels:
                details.append(f"labels={', '.join(label.name for label in story.item.labels)}")
            if story.item.story_points is not None:
                details.append(f"points={story.item.story_points.value}")
        suffix = f" — {', '.join(details)}" if details else ""
        typer.echo(f"  - {story.title} (stories.md:{story.line}){suffix}")
        if state is not None:
            _show_item_status(story.story_id, state.stories.get(story.story_id), story.source_hash)
    if state is not None:
        missing_ids = _missing_story_ids(preview, state)
        if missing_ids:
            typer.echo("Recorded stories missing from stories.md:", err=True)
            for story_id in sorted(missing_ids):
                record = state.stories[story_id]
                detail = record.key or "unresolved attempt"
                typer.echo(f"  - {story_id}: {detail}", err=True)
    if preview.errors:
        typer.echo(f"Errors ({len(preview.errors)}):", err=True)
        for error in preview.errors:
            typer.echo(f"  - {error}", err=True)


def _show_item_status(item: str, record: ItemRecord | None, source_hash: str | None) -> None:
    if record is None:
        typer.echo(f"    {item}: to create")
    elif record.status == "pending":
        typer.echo(f"    {item}: unresolved attempt; resolve before importing", err=True)
    else:
        typer.echo(f"    {item}: already uploaded as {record.key}; will skip")
    if record is not None and source_hash is not None and record.source_hash != source_hash:
        typer.echo(f"    {item}: Markdown changed since recorded attempt; Jira will not be updated", err=True)


def _has_pending(state: ImportState) -> bool:
    return state.epic is not None and state.epic.status == "pending" or any(
        record.status == "pending" for record in state.stories.values()
    )


def _missing_story_ids(preview: FolderPreview, state: ImportState) -> set[str]:
    return set(state.stories) - {story.story_id for story in preview.stories}


def _definitely_rejected(error: Exception) -> bool:
    return (
        isinstance(error, httpx.HTTPStatusError)
        and 400 <= error.response.status_code < 500
        and error.response.status_code not in (408, 429)
    )


def _preview_folder(folder_path: str) -> bool:
    preview = load_folder_preview(folder_path)
    try:
        state = ImportState.load(Path(folder_path)) if Path(folder_path).is_dir() else None
    except ValueError as error:
        _show_folder_preview(preview)
        typer.echo(str(error), err=True)
        return False
    _show_folder_preview(preview, state)
    if state is not None and state.base_url is None:
        typer.echo("No resume record; existing Jira items cannot be identified locally.")
    return not preview.errors and (
        state is None or not _has_pending(state) and not _missing_story_ids(preview, state)
    )


async def _import_folder(folder_path: str, yes: bool = False) -> bool:
    try:
        folder = Path(folder_path)
        if not folder.is_dir():
            _show_folder_preview(load_folder_preview(folder_path))
            return False
        with lock_folder(folder):
            return await _import_locked(folder, yes)
    except (OSError, ValueError) as error:
        typer.echo(f"Error importing folder: {error}", err=True)
        return False


async def _import_locked(folder: Path, yes: bool) -> bool:
    preview = load_folder_preview(str(folder))
    try:
        state = ImportState.load(folder)
    except ValueError as error:
        _show_folder_preview(preview)
        typer.echo(str(error), err=True)
        return False
    _show_folder_preview(preview, state)
    if preview.errors or _has_pending(state):
        return False
    result = _command_result.get()
    if result is not None:
        result.error_code = "IMPORT_FAILED"
    if _missing_story_ids(preview, state):
        typer.echo(
            "Rebind or retire missing story IDs before importing new items.", err=True
        )
        return False
    if state.base_url is None:
        typer.echo("No resume record; existing Jira items cannot be identified locally.")
    provider = None
    if state.base_url is not None:
        try:
            provider = container.get_jira_provider()
            state.require_destination(provider.settings.base_url, provider.settings.project_key)
        except Exception as error:
            typer.echo(f"Error importing folder: {error}", err=True)
            return False
    if state.epic is not None and all(
        story.story_id in state.stories for story in preview.stories
    ):
        _record("no_change", _preview_data(preview, state))
        typer.echo("All work items are already uploaded.")
        return True
    if not yes:
        if _output_format.get() == OutputFormat.json or not sys.stdin.isatty():
            typer.echo("Use --yes to create items without an interactive terminal.", err=True)
            _record("error", _preview_data(preview, state), "CONFIRMATION_REQUIRED")
            return False
        if not typer.confirm("Create these work items in Jira?", default=False):
            _record("cancelled", _preview_data(preview, state))
            typer.echo("Import cancelled; no work items were created.")
            return True

    if provider is None:
        try:
            provider = container.get_jira_provider()
            state.require_destination(provider.settings.base_url, provider.settings.project_key)
        except Exception as error:
            typer.echo(f"Error importing folder: {error}", err=True)
            return False

    assert preview.epic is not None and preview.epic_hash is not None
    if state.epic is None:
        state.epic = ItemRecord("pending", preview.epic_hash)
        try:
            state.save()
            issue_key = await provider.create_epic(preview.epic)
            _created("epic", issue_key)
            state.epic = ItemRecord("created", preview.epic_hash, issue_key)
            state.save()
            typer.echo(f"Successfully created epic: {issue_key}")
        except Exception as error:
            if _definitely_rejected(error):
                state.epic = None
                state.save()
            typer.echo(f"Error importing folder: {error}", err=True)
            return False

    assert state.epic.key is not None
    all_created = True
    for story_preview in preview.stories:
        if story_preview.story_id in state.stories:
            continue
        assert story_preview.item is not None
        state.stories[story_preview.story_id] = ItemRecord("pending", story_preview.source_hash)
        try:
            state.save()
            story_key = await provider.create_story(story_preview.item, epic_key=state.epic.key)
            _created("story", story_key, story_preview.story_id)
            state.stories[story_preview.story_id] = ItemRecord(
                "created", story_preview.source_hash, story_key
            )
            state.save()
            typer.echo(f"Successfully created story: {story_key} ({story_preview.title})")
        except Exception as error:
            typer.echo(f"Error creating story '{story_preview.title}': {error}", err=True)
            if _definitely_rejected(error):
                del state.stories[story_preview.story_id]
                try:
                    state.save()
                except OSError as save_error:
                    typer.echo(f"Cannot save import state: {save_error}", err=True)
                    return False
                all_created = False
                continue
            typer.echo("Uncertain Jira outcome; resolve this item before retrying.", err=True)
            return False
    if all_created:
        result = _command_result.get()
        if result is not None:
            result.status = "completed"
    return all_created


async def _resolve_import(folder_path: str, item: str, key: str | None, retry: bool) -> bool:
    try:
        folder = Path(folder_path)
        if not folder.is_dir():
            raise ValueError(f"Folder not found: {folder}")
        with lock_folder(folder):
            state = ImportState.load(folder)
            record = state.epic if item == "epic" else state.stories.get(item)
            if record is None or record.status != "pending":
                raise ValueError(f"No unresolved attempt for {item}")
            provider = container.get_jira_provider()
            state.require_destination(provider.settings.base_url, provider.settings.project_key)
            if key is not None:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", key) or not key.startswith(
                    f"{state.project_key}-"
                ):
                    raise ValueError(f"Jira key must belong to project {state.project_key}")
                if item == "epic":
                    if await provider.fetch_epic(key) is None:
                        raise ValueError(f"Jira issue {key} was not found")
                else:
                    story = await provider.fetch_story(key)
                    if story is None:
                        raise ValueError(f"Jira issue {key} was not found")
                    if state.epic is None or story.epic_key != state.epic.key:
                        raise ValueError(f"Story {key} is not linked to the recorded epic")
                resolved = ItemRecord("created", record.source_hash, key)
            if retry:
                if item == "epic":
                    state.epic = None
                else:
                    del state.stories[item]
            else:
                if item == "epic":
                    state.epic = resolved
                else:
                    state.stories[item] = resolved
            state.save()
        typer.echo(f"Resolved {item}: {'ready to retry' if retry else key}")
        _record("completed", {"item": item, "key": key, "action": "retry" if retry else "bind"})
        return True
    except Exception as error:
        typer.echo(f"Error resolving import: {error}", err=True)
        return False


def _reconcile_missing_story(folder_path: str, old_id: str, new_id: str | None) -> bool:
    try:
        folder = Path(folder_path)
        if not folder.is_dir():
            raise ValueError(f"Folder not found: {folder}")
        with lock_folder(folder):
            preview = load_folder_preview(str(folder))
            if preview.errors:
                raise ValueError("Fix folder validation errors before reconciling stories")
            state = ImportState.load(folder)
            current_ids = {story.story_id for story in preview.stories}
            record = state.stories.get(old_id)
            if record is None or record.status != "created" or old_id in current_ids:
                raise ValueError(f"{old_id} is not a missing uploaded story")
            if new_id is not None:
                if new_id not in current_ids or new_id in state.stories:
                    raise ValueError(f"{new_id} must be a new story ID in stories.md")
                state.stories[new_id] = record
            del state.stories[old_id]
            state.save()
        action = f"rebound to {new_id}" if new_id is not None else "retired"
        typer.echo(f"Story {old_id} {action}.")
        _record("completed", {"story_id": old_id, "new_story_id": new_id})
        return True
    except Exception as error:
        typer.echo(f"Error reconciling import: {error}", err=True)
        return False


def _prompt_item(include_points: bool = False) -> WorkItemInput:
    title = inquirer.text(message="Title:").execute()
    description = inquirer.text(message="Description (optional):").execute()
    priority = inquirer.text(message="Priority (optional):").execute()
    labels_text = inquirer.text(message="Labels, comma-separated (optional):").execute()
    labels = tuple(label.strip() for label in labels_text.split(",") if label.strip())
    points = None
    if include_points:
        points_text = inquirer.text(message="Story points, whole number (optional):").execute()
        if points_text.strip():
            points = int(points_text)
            if points < 0:
                raise ValueError("Story points cannot be negative")
    return WorkItemInput(title, description, priority, labels, points)


def _mask_token(token: str) -> str:
    return f"****{token[-4:]}" if len(token) >= 12 else "****"


def _config_failure(action: str, error: Exception, code: str = "COMMAND_FAILED") -> bool:
    typer.echo(f"Error {action}: {error}", err=True)
    _record("error", {}, code)
    return False


def _config_setup(base_url: str | None, email: str | None, project_key: str | None) -> bool:
    if _output_format.get() == OutputFormat.json:
        return _config_failure(
            "configuring Jira",
            ValueError("setup prompts for the API token; run it without --format json"),
            "INVALID_ARGUMENT",
        )
    try:
        stored = container.config_store.load_credentials()
        projects = container.config_store.list_projects()
        base_url = base_url or typer.prompt("Jira base URL", default=stored.base_url if stored else None)
        email = email or typer.prompt("Atlassian email", default=stored.email if stored else None)
        if not project_key and not projects:
            project_key = typer.prompt("Project key")
        api_token = typer.prompt(
            "API token (hidden" + ("; Enter keeps the stored one" if stored else "") + ")",
            default="", hide_input=True, show_default=False,
        ) or (stored.api_token if stored else "")
        credentials = JiraCredentials(base_url, email, api_token)
        if project_key:
            normalize_project_key(project_key)
    except BusinessRuleViolation as error:
        return _config_failure("configuring Jira", error, "VALIDATION_FAILED")
    except (typer.Abort, KeyboardInterrupt):
        typer.echo("Aborted; nothing was saved.", err=True)
        _record("cancelled", {})
        return False
    except Exception as error:
        return _config_failure("configuring Jira", error)
    try:
        container.config_store.save_credentials(credentials)
        if project_key:
            container.config_store.add_project(project_key)
    except Exception as error:
        return _config_failure("configuring Jira", error)
    container.reset()
    _record("completed", {"base_url": credentials.base_url, "email": credentials.email})
    typer.echo(f"Saved Jira connection for {credentials.email} at {credentials.base_url}.")
    return True


def _config_projects(action: str, project_key: str, make_default: bool = False) -> bool:
    store = container.config_store
    try:
        if action == "add":
            store.add_project(project_key, make_default)
        elif action == "default":
            store.set_default_project(project_key)
        else:
            store.remove_project(project_key)
        projects = store.list_projects()
    except BusinessRuleViolation as error:
        return _config_failure("updating projects", error, "VALIDATION_FAILED")
    except Exception as error:
        return _config_failure("updating projects", error)
    container.reset()
    data = {"projects": [{"key": project.key, "default": project.is_default} for project in projects]}
    _record("completed", data)
    typer.echo(_projects_line(projects))
    return True


def _projects_line(projects) -> str:
    if not projects:
        return "Projects: none configured."
    return "Projects: " + ", ".join(f"{project.key}{' (default)' if project.is_default else ''}" for project in projects)


def _config_show() -> bool:
    try:
        connection, _ = container.resolve_jira_connection()
    except Exception as error:
        return _config_failure("reading Jira settings", error)
    projects = connection.projects
    fields = {
        "base_url": connection.base_url,
        "email": connection.email,
        "api_token": connection.api_token,
        "project_key": connection.project_key,
    }

    def shown(name: str, resolved: ResolvedValue | None) -> dict | None:
        if resolved is None:
            return None
        value = _mask_token(resolved.value) if name == "api_token" else resolved.value
        return {"value": value, "source": resolved.source}

    data: dict = {name: shown(name, resolved) for name, resolved in fields.items()}
    data["projects"] = [{"key": project.key, "default": project.is_default} for project in projects]
    _record("completed", data)
    for name in fields:
        entry = data[name]
        typer.echo(f"{name:<12} {entry['value']}  ({entry['source']})" if entry else f"{name:<12} not set")
    typer.echo(_projects_line(projects))
    if connection.store_error:
        typer.echo(f"Warning: the stored config could not be read: {connection.store_error}", err=True)
    if any(data[name] is None for name in fields):
        typer.echo("Run `dwire config setup` to fill the missing values.")
    return True


def _config_clear(yes: bool) -> bool:
    if not yes:
        if _output_format.get() == OutputFormat.json or not sys.stdin.isatty():
            typer.echo("Use --yes to clear the stored Jira settings without an interactive terminal.", err=True)
            _record("error", {}, "CONFIRMATION_REQUIRED")
            return False
        if not typer.confirm("Delete the stored Jira connection and projects?", default=False):
            _record("cancelled", {})
            typer.echo("Nothing was deleted.")
            return True
    try:
        container.config_store.clear()
    except Exception as error:
        return _config_failure("clearing Jira settings", error)
    container.reset()
    _record("completed", {})
    typer.echo("Stored Jira settings deleted.")
    return True


_CONFIG_SHOW = "Show current settings"
_CONFIG_SETUP = "Set up connection (URL, email, API token)"
_CONFIG_ADD_PROJECT = "Add a project"
_CONFIG_SET_DEFAULT = "Choose the default project"
_CONFIG_REMOVE_PROJECT = "Remove a project"
_CONFIG_BACK = "Back"


def _change_project(action: str, message: str) -> bool:
    try:
        projects = container.config_store.list_projects()
    except Exception as error:
        typer.echo(f"Error reading projects: {error}", err=True)
        return True
    if not projects:
        typer.echo("No projects configured yet. Add one first.")
        return True
    picked = inquirer.select(
        message=message,
        choices=[
            *(Choice(value=project.key, name=f"{project.key}{' (default)' if project.is_default else ''}")
              for project in projects),
            Choice(value=None, name=_CONFIG_BACK),
        ],
    ).execute()
    if picked is None:
        return False
    _config_projects(action, picked)
    return True


def _configure_menu() -> bool:
    """Sub-menu mirroring `dwire config`; clearing stays CLI-only because it is destructive.

    Returns False when nothing was shown that needs a "press Enter" pause (Back or Ctrl-C).
    """
    try:
        choice = inquirer.select(
            message="Configure:",
            choices=[
                _CONFIG_SHOW, _CONFIG_SETUP, _CONFIG_ADD_PROJECT,
                _CONFIG_SET_DEFAULT, _CONFIG_REMOVE_PROJECT, _CONFIG_BACK,
            ],
        ).execute()
        if choice == _CONFIG_BACK:
            return False
        if choice == _CONFIG_SHOW:
            _config_show()
        elif choice == _CONFIG_SETUP:
            _config_setup(None, None, None)
        elif choice == _CONFIG_ADD_PROJECT:
            key = inquirer.text(message="Project key (e.g. PROJ):").execute()
            try:
                normalize_project_key(key)
            except BusinessRuleViolation as error:
                typer.echo(f"Error updating projects: {error}", err=True)
                return True
            make_default = inquirer.confirm(message="Make it the default project?", default=False).execute()
            _config_projects("add", key, make_default)
        elif choice == _CONFIG_SET_DEFAULT:
            return _change_project("default", "Default project:")
        elif choice == _CONFIG_REMOVE_PROJECT:
            return _change_project("remove", "Remove project:")
        return True
    except KeyboardInterrupt:
        return False


def _run_interactive_menu() -> None:
    """Keyboard-navigable main menu, shown when `dwire` has no subcommand."""
    while True:
        choice = run_menu(_MENU_GROUPS, _EXIT)

        if choice == _EXIT:
            break
        click.clear()
        try:
            if choice == _FETCH_EPIC:
                key = inquirer.text(message="Epic key (e.g. PROJ-123):").execute()
                asyncio.run(_fetch_epic(key))
            elif choice == _FETCH_STORY:
                key = inquirer.text(message="Story key (e.g. PROJ-124):").execute()
                asyncio.run(_fetch_story(key))
            elif choice == _LIST_STORIES:
                key = inquirer.text(message="Epic key (e.g. PROJ-123):").execute()
                asyncio.run(_list_stories(key))
            elif choice == _LIST_ASSIGNED:
                account_id = inquirer.text(
                    message="Account ID (leave blank for current user):"
                ).execute().strip()
                asyncio.run(_list_assigned(account_id or None))
            elif choice == _SEARCH:
                if not run_search_menu(work_actions):
                    continue
            elif choice == _MANAGE:
                if not run_work_menu(work_actions):
                    continue
            elif choice == _CREATE_EPIC:
                asyncio.run(_create_epic(_prompt_item()))
            elif choice == _CREATE_STORY:
                key = inquirer.text(message="Parent epic key (e.g. PROJ-123):").execute()
                asyncio.run(_create_story(key, _prompt_item(include_points=True)))
            elif choice == _PREVIEW_FOLDER:
                folder_path = inquirer.text(
                    message="Folder containing epic.md and optional stories.md:"
                ).execute()
                _preview_folder(folder_path)
            elif choice == _CONFIGURE:
                if not _configure_menu():
                    continue
            elif choice == _IMPORT_FOLDER:
                folder_path = inquirer.text(
                    message="Folder containing epic.md and optional stories.md:"
                ).execute()
                asyncio.run(_import_folder(folder_path))
        except ValueError as error:
            typer.echo(f"Invalid input: {error}", err=True)
        inquirer.confirm(
            message="Press Enter to return to the menu...", default=True
        ).execute()


if __name__ == "__main__":
    app()
