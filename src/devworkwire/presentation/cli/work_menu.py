import asyncio
from dataclasses import asdict

import typer
from InquirerPy import inquirer
from InquirerPy.base.control import Choice

from devworkwire.core.ports.work_item_changes import SearchFilters, StoryUpdate
from devworkwire.features.progress.application.transitions import TransitionRequest
from devworkwire.presentation.cli.work_actions import WorkActions
from devworkwire.presentation.cli.theme import prompt_style


def _text(message: str) -> str:
    return inquirer.text(style=prompt_style(), message=message).execute()


def _values(message: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in _text(message).split(",") if value.strip())


def _story_update() -> StoryUpdate | None:
    fields = inquirer.checkbox(
        style=prompt_style(),
        message="Fields to change (Space selects; unselected fields stay unchanged):",
        choices=[
            Choice(value=field, name=name)
            for field, name in (
                ("title", "Title"),
                ("description", "Description"),
                ("priority", "Priority"),
                ("labels", "Labels"),
                ("points", "Story points"),
                ("epic_key", "Parent epic"),
            )
        ],
    ).execute()
    if not fields:
        return None
    title = _text("New title:") if "title" in fields else None
    description = (
        _text("New Markdown description (blank clears it):")
        if "description" in fields
        else None
    )
    priority = _text("New priority:") if "priority" in fields else None
    labels = (
        _values("Replacement labels, comma-separated (blank clears them):")
        if "labels" in fields
        else None
    )
    points_text = (
        _text("Story points (blank clears them):").strip()
        if "points" in fields
        else None
    )
    epic = (
        _text("Parent epic key (blank removes the parent):").strip()
        if "epic_key" in fields
        else None
    )
    return StoryUpdate(
        title=title,
        description=description,
        priority=priority,
        labels=labels,
        points=int(points_text) if points_text else None,
        epic_key=epic or None,
        clear_points=points_text == "",
        clear_epic=epic == "",
    )


def run_work_menu(actions: WorkActions) -> bool:
    try:
        choice = inquirer.select(
            style=prompt_style(),
            message="Manage work:",
            choices=[
                "Edit story",
                "Assign issue",
                "Change status",
                "List transitions",
                "Back",
            ],
        ).execute()
        if choice == "Back":
            return False
        key = _text("Issue key:").strip()
        if choice == "Edit story":
            update = _story_update()
            if update is None:
                return False
            for field, value in asdict(update).items():
                if value is not None and value is not False:
                    typer.echo(f"{field}: {value!r}")
            if inquirer.confirm(
                style=prompt_style(),
                message=f"Apply these changes to {key}?", default=False
            ).execute():
                asyncio.run(actions.update(key, update))
        elif choice == "Assign issue":
            mode = inquirer.select(
                style=prompt_style(),
                message="Assign to:",
                choices=["Current user", "Account ID", "Unassigned", "Back"],
            ).execute()
            if mode == "Back":
                return False
            assignee = (
                "me"
                if mode == "Current user"
                else (
                    _text("Jira account ID:").strip() if mode == "Account ID" else None
                )
            )
            if inquirer.confirm(
                style=prompt_style(),
                message=f"Assign {key} to {assignee or 'unassigned'}?",
                default=False,
            ).execute():
                asyncio.run(actions.assign(key, assignee, mode == "Unassigned"))
        elif choice == "List transitions":
            asyncio.run(actions.transitions(key))
        elif choice == "Change status":
            transitions = asyncio.run(actions.available_transitions(key))
            if not transitions:
                typer.echo("No transitions available.")
                return True
            selected = inquirer.select(
                style=prompt_style(),
                message="Available transitions:",
                choices=[
                    *(
                        Choice(
                            value=item.id,
                            name=f"{item.name} → {item.status} [{item.id}]",
                        )
                        for item in transitions
                    ),
                    Choice(value=None, name="Back"),
                ],
            ).execute()
            if selected is None:
                return False
            if inquirer.confirm(
                style=prompt_style(),
                message=f"Apply transition {selected} to {key}?", default=False
            ).execute():
                asyncio.run(
                    actions.transition(key, TransitionRequest(transition_id=selected))
                )
        return True
    except (KeyboardInterrupt, EOFError):
        return False
    except Exception as error:
        typer.echo(f"Error managing work: {error}", err=True)
        return True


def run_search_menu(actions: WorkActions) -> bool:
    try:
        mode = inquirer.select(
            style=prompt_style(),
            message="Search with:", choices=["Filters", "Raw JQL", "Back"]
        ).execute()
        if mode == "Back":
            return False
        if mode == "Raw JQL":
            filters = SearchFilters(
                jql=_text("JQL (runs as written, across accessible projects):")
            )
        else:
            filters = SearchFilters(
                statuses=_values("Statuses, comma-separated (blank for any):"),
                labels=_values("Required labels, comma-separated (blank for any):"),
                issue_types=_values("Issue types, comma-separated (blank for any):"),
            )
        asyncio.run(actions.search(filters))
        return True
    except (KeyboardInterrupt, EOFError):
        return False
    except ValueError as error:
        typer.echo(f"Invalid search: {error}", err=True)
        return True
