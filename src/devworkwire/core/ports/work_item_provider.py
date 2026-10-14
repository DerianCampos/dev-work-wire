from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from devworkwire.core.domain import Epic, UserStory
from devworkwire.core.ports.work_item_changes import StoryUpdate, TransitionSummary


@dataclass(frozen=True)
class WorkItemSummary:
    key: str
    issue_type: str
    title: str
    status: str


class WorkItemProvider(ABC):
    @abstractmethod
    async def get_issue_type(self, key: str) -> str | None:
        pass

    @abstractmethod
    async def update_story(self, key: str, update: StoryUpdate) -> None:
        pass

    @abstractmethod
    async def current_account_id(self) -> str:
        pass

    @abstractmethod
    async def assign_issue(self, key: str, account_id: str | None) -> None:
        pass

    @abstractmethod
    async def list_transitions(self, key: str) -> list[TransitionSummary]:
        pass

    @abstractmethod
    async def transition_issue(self, key: str, transition_id: str) -> None:
        pass

    @abstractmethod
    async def search_work_items(self, jql: str) -> list[WorkItemSummary]:
        pass

    @abstractmethod
    async def fetch_epic(self, key: str) -> Optional[Epic]:
        """Fetch an Epic by its key."""
        pass

    @abstractmethod
    async def create_epic(self, epic: Epic) -> str:
        """Create an Epic and return its new issue key."""
        pass

    @abstractmethod
    async def create_story(self, story: UserStory, epic_key: str) -> str:
        """Create a Story linked to the given Epic key and return its new issue key."""
        pass

    @abstractmethod
    async def fetch_story(self, key: str) -> Optional[UserStory]:
        """Fetch a Story by its key."""
        pass

    @abstractmethod
    async def list_stories(self, epic_key: str) -> list[UserStory]:
        """Fetch all Stories linked to an Epic."""
        pass

    @abstractmethod
    async def list_assigned_work_items(
        self, account_id: str | None = None
    ) -> list[WorkItemSummary]:
        """Fetch open work assigned to a user in the configured project."""
        pass
