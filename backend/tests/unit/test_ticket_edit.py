import pytest
from pydantic import ValidationError

from tier0.agent.state import PendingTicket
from tier0.tickets.drafting import DESCRIPTION_CHARS, SUMMARY_CHARS, TicketEdit

VALID = {"title": "Monitor flickers", "description": "It flickers when docked.", "category": "IT"}


def test_a_valid_edit_keeps_what_the_employee_wrote() -> None:
    edit = TicketEdit.model_validate({**VALID, "priority": "High"})
    assert (edit.title, edit.category, edit.priority) == ("Monitor flickers", "IT", "High")


def test_priority_is_optional() -> None:
    assert TicketEdit.model_validate(VALID).priority is None


@pytest.mark.parametrize("field", ["title", "description"])
@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_title_or_description_is_refused(field: str, blank: str) -> None:
    with pytest.raises(ValidationError):
        TicketEdit.model_validate({**VALID, field: blank})


def test_the_title_is_one_line() -> None:
    edit = TicketEdit.model_validate({**VALID, "title": "  Monitor\n flickers\twhen   docked "})
    assert edit.title == "Monitor flickers when docked"


def test_the_description_keeps_its_paragraphs_but_loses_control_characters() -> None:
    edit = TicketEdit.model_validate({**VALID, "description": "First.\n\nSecond.\x00\x07 Third."})
    assert edit.description.startswith("First.\n\nSecond.") and edit.description.endswith("Third.")
    assert "\x00" not in edit.description and "\x07" not in edit.description


def test_the_length_limits_match_drafting() -> None:
    TicketEdit.model_validate({**VALID, "title": "t" * SUMMARY_CHARS})
    TicketEdit.model_validate({**VALID, "description": "d" * DESCRIPTION_CHARS})
    with pytest.raises(ValidationError):
        TicketEdit.model_validate({**VALID, "title": "t" * (SUMMARY_CHARS + 1)})
    with pytest.raises(ValidationError):
        TicketEdit.model_validate({**VALID, "description": "d" * (DESCRIPTION_CHARS + 1)})


@pytest.mark.parametrize(
    "bad", [{"category": "Finance"}, {"category": None}, {"priority": "Urgent"}, {"extra": 1}]
)
def test_only_known_categories_priorities_and_fields_are_accepted(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TicketEdit.model_validate({**VALID, **bad})


def test_an_edit_replaces_the_draft_but_keeps_the_submission_id() -> None:
    draft = PendingTicket(
        summary="old", description="old", category="HR", priority="Low", submission_id="abc"
    )
    updated = TicketEdit.model_validate({**VALID, "priority": None}).apply_to(draft)
    assert (updated.summary, updated.category, updated.priority) == (VALID["title"], "IT", None)
    assert updated.submission_id == "abc" and draft.summary == "old"  # the original is untouched
