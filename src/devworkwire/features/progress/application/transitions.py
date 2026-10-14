from dataclasses import dataclass

from devworkwire.core.domain.exceptions import InvalidTransitionException
from devworkwire.core.ports.work_item_changes import (
    TransitionSummary,
    validate_issue_key,
)
from devworkwire.core.ports.work_item_provider import WorkItemProvider


@dataclass(frozen=True)
class TransitionRequest:
    status: str | None = None
    transition_id: str | None = None

    def __post_init__(self) -> None:
        if (self.status is None) == (self.transition_id is None):
            raise ValueError("Choose exactly one of --status or --transition-id")
        if self.status is not None and not self.status.strip():
            raise ValueError("Status cannot be empty")
        if self.transition_id is not None and not self.transition_id.isdecimal():
            raise ValueError("Transition ID must be numeric")


def select_transition(
    transitions: list[TransitionSummary],
    request: TransitionRequest,
    overrides: dict[str, str],
) -> TransitionSummary:
    if request.transition_id is not None:
        matches = [
            transition
            for transition in transitions
            if transition.id == request.transition_id
        ]
    else:
        status = (request.status or "").strip().casefold()
        alias = next(
            (value for key, value in overrides.items() if key.casefold() == status),
            None,
        )
        matches = [
            transition
            for transition in transitions
            if (
                transition.name.casefold() == alias.casefold()
                if alias is not None
                else transition.status.casefold() == status
            )
        ]
    if len(matches) != 1:
        choices = (
            ", ".join(f"{item.id}: {item.name} → {item.status}" for item in transitions)
            or "none"
        )
        reason = (
            "Ambiguous transition" if matches else "No available transition matches"
        )
        raise InvalidTransitionException(
            f"{reason}. Available: {choices}. Use --transition-id."
        )
    return matches[0]


class ProgressActions:
    def __init__(self, provider: WorkItemProvider, overrides: dict[str, str]):
        self.provider = provider
        self.overrides = overrides

    async def change_status(
        self, key: str, request: TransitionRequest
    ) -> TransitionSummary:
        validate_issue_key(key)
        transitions = await self.provider.list_transitions(key)
        selected = select_transition(transitions, request, self.overrides)
        await self.provider.transition_issue(key, selected.id)
        return selected
