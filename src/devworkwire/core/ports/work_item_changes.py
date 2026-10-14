import re
from dataclasses import dataclass

from devworkwire.core.domain import Label, StoryPoints


def validate_issue_key(key: str) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", key):
        raise ValueError(f"Invalid Jira issue key: {key}")


def validate_account_id(account_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9:_-]+", account_id) or account_id == "-1":
        raise ValueError("Invalid Jira account ID")


@dataclass(frozen=True)
class StoryUpdate:
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    labels: tuple[str, ...] | None = None
    points: int | None = None
    epic_key: str | None = None
    clear_points: bool = False
    clear_epic: bool = False

    def __post_init__(self) -> None:
        if self.title is not None and not self.title.strip():
            raise ValueError("Story title cannot be empty")
        if self.priority is not None and not self.priority.strip():
            raise ValueError("Priority cannot be empty")
        if self.labels is not None:
            for label in self.labels:
                Label(label)
        if self.points is not None:
            StoryPoints(self.points)
        if self.epic_key is not None:
            validate_issue_key(self.epic_key)
        if self.clear_points and self.points is not None:
            raise ValueError("Choose --points or --clear-points")
        if self.clear_epic and self.epic_key is not None:
            raise ValueError("Choose --epic or --clear-epic")
        if all(
            value is None
            for value in (
                self.title,
                self.description,
                self.priority,
                self.labels,
                self.points,
                self.epic_key,
            )
        ) and not (self.clear_points or self.clear_epic):
            raise ValueError("Supply at least one story field to update")


@dataclass(frozen=True)
class SearchFilters:
    statuses: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    issue_types: tuple[str, ...] = ()
    jql: str | None = None

    def __post_init__(self) -> None:
        if self.jql is not None:
            if not self.jql.strip():
                raise ValueError("JQL cannot be empty")
            if self.statuses or self.labels or self.issue_types:
                raise ValueError("Choose --jql or filter flags, not both")
        for value in (*self.statuses, *self.labels, *self.issue_types):
            if not value.strip() or any(ord(character) < 32 for character in value):
                raise ValueError(
                    "Search filters must be nonempty and contain no control characters"
                )


@dataclass(frozen=True)
class TransitionSummary:
    id: str
    name: str
    status: str
    required_fields: tuple[str, ...] = ()
