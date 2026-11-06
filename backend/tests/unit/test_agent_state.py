import types
import typing
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import BaseModel

from tier0.agent.checkpoint import ALLOWED_STATE_TYPES
from tier0.agent.state import (
    ALLOWED_TRANSITIONS,
    AgentState,
    IllegalTransition,
    PendingTicket,
    WorkflowStatus,
    initial_state,
    transition,
    workflow_of,
)

S = WorkflowStatus


def reachable_from(start: WorkflowStatus) -> set[WorkflowStatus]:
    seen, queue = {start}, [start]
    while queue:
        for nxt in ALLOWED_TRANSITIONS[queue.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def test_every_status_has_a_transition_entry_and_can_reach_idle() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(WorkflowStatus)
    for status in WorkflowStatus:
        assert S.IDLE in reachable_from(status), status


def test_a_ticket_can_only_be_submitted_straight_after_confirmation() -> None:
    entering = {s for s, targets in ALLOWED_TRANSITIONS.items() if S.SUBMITTING_TICKET in targets}
    assert entering == {S.AWAITING_TICKET_CONFIRMATION}


@pytest.mark.parametrize("state", [S.IDLE, S.OFFERED_TICKET, S.COLLECTING_TICKET_FIELDS])
def test_submission_cannot_skip_the_confirmation_step(state: WorkflowStatus) -> None:
    with pytest.raises(IllegalTransition, match="submitting_ticket"):
        transition(state, S.SUBMITTING_TICKET)


def test_the_happy_path_is_allowed_step_by_step() -> None:
    path = [
        S.IDLE,
        S.OFFERED_TICKET,
        S.COLLECTING_TICKET_FIELDS,
        S.AWAITING_TICKET_CONFIRMATION,
        S.SUBMITTING_TICKET,
        S.IDLE,
    ]
    for current, target in zip(path, path[1:], strict=False):
        assert transition(current, target) is target


def test_declining_or_ambiguity_never_reach_submission() -> None:
    assert transition(S.AWAITING_TICKET_CONFIRMATION, S.IDLE) is S.IDLE
    assert transition(S.AWAITING_TICKET_CONFIRMATION, S.AWAITING_TICKET_CONFIRMATION)
    assert transition(
        S.SUBMITTING_TICKET, S.AWAITING_TICKET_CONFIRMATION
    )  # retry after outcome unknown


def test_selecting_a_ticket_only_returns_to_idle() -> None:
    assert transition(S.IDLE, S.AWAITING_TICKET_SELECTION)
    assert ALLOWED_TRANSITIONS[S.AWAITING_TICKET_SELECTION] == {S.AWAITING_TICKET_SELECTION, S.IDLE}


def test_pending_ticket_reports_what_is_still_missing() -> None:
    assert PendingTicket().missing_fields() == ["summary", "description"]
    assert PendingTicket(summary="VPN down", description="  ").missing_fields() == ["description"]
    done = PendingTicket(summary="VPN down", description="Cannot connect.")
    assert done.is_complete and done.category is None and done.priority is None


def test_initial_state_starts_idle_with_the_signed_in_email_and_no_history() -> None:
    state = initial_state("alice@buzztech.example")
    assert state["requester_email"] == "alice@buzztech.example"
    assert state["workflow"] is S.IDLE and state["turns"] == []
    assert state["pending_ticket"] is None and state["ticket_choices"] == []


def test_workflow_of_restores_the_enum_from_the_plain_string_checkpoints_return() -> None:
    restored: Any = {"workflow": "awaiting_ticket_confirmation"}
    assert workflow_of(restored) is S.AWAITING_TICKET_CONFIRMATION
    assert workflow_of(AgentState()) is S.IDLE


def collect_types(annotation: Any, found: set[type]) -> None:
    if isinstance(annotation, type) and annotation.__module__.startswith("tier0"):
        if annotation in found:
            return
        found.add(annotation)
        if issubclass(annotation, BaseModel):
            for field in annotation.model_fields.values():
                collect_types(field.annotation, found)
    for arg in get_args(annotation):
        collect_types(arg, found)
    if isinstance(annotation, types.UnionType | type(typing.Union[int, str])):  # noqa: UP007
        for arg in get_args(annotation):
            collect_types(arg, found)


def test_every_class_stored_in_the_state_is_on_the_checkpoint_allowlist() -> None:
    """Adding a field of a new type to AgentState must also register it for restoring."""
    found: set[type] = set()
    for annotation in get_type_hints(AgentState, include_extras=False).values():
        collect_types(annotation, found)
    needed = {(cls.__module__, cls.__name__) for cls in found}
    assert needed, "expected the state to contain tier0 types"
    assert needed <= set(ALLOWED_STATE_TYPES), sorted(needed - set(ALLOWED_STATE_TYPES))
