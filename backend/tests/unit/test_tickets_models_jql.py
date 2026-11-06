import pytest
from pydantic import ValidationError

from tier0.tickets.jql import quote
from tier0.tickets.models import TicketDraft, validate_email, validate_ticket_key


def draft(**overrides: object) -> TicketDraft:
    base: dict[str, object] = {
        "requester_email": "alice@buzztech.example",
        "summary": "Monitor flickers",
        "description": "It started on Monday.",
        "category": "IT",
    }
    return TicketDraft.model_validate({**base, **overrides})


def test_quote_wraps_and_escapes_quotes_and_backslashes() -> None:
    assert quote("a@b.com") == '"a@b.com"'
    assert quote('x" OR project = "OTHER') == '"x\\" OR project = \\"OTHER"'
    assert quote("back\\slash") == '"back\\\\slash"'


@pytest.mark.parametrize("value", ["line\nbreak", "tab\there", "nul\x00", "del\x7f"])
def test_quote_refuses_control_characters(value: str) -> None:
    with pytest.raises(ValueError, match="control"):
        quote(value)


@pytest.mark.parametrize(
    "email", ["", "plain", "a@b", 'a"b@x.com', "a b@x.com", "a@b.com\nbcc: x@y.com", "<a@b.com>"]
)
def test_invalid_emails_are_rejected(email: str) -> None:
    with pytest.raises(ValueError, match="email"):
        validate_email(email)


def test_valid_email_is_trimmed() -> None:
    assert validate_email("  alice@buzztech.example ") == "alice@buzztech.example"


@pytest.mark.parametrize("key", ["SUP-1", "AB_C-20"])
def test_valid_keys(key: str) -> None:
    assert validate_ticket_key(key) == key


@pytest.mark.parametrize("key", ["sup-1", "SUP-", "SUP-1 OR 1=1", 'SUP-1"', "1-SUP", ""])
def test_malformed_keys_are_rejected(key: str) -> None:
    with pytest.raises(ValueError, match="key"):
        validate_ticket_key(key)


def test_draft_accepts_a_complete_ticket() -> None:
    d = draft(priority="High", submission_id="3f2b8c1e-aaaa-bbbb")
    assert d.priority == "High" and d.category == "IT"


@pytest.mark.parametrize(
    "overrides",
    [
        {"requester_email": "nope"},
        {"summary": ""},
        {"summary": "two\nlines"},
        {"summary": "x" * 256},
        {"description": "  "},
        {"category": "Facilities"},
        {"priority": "Urgent"},
        {"submission_id": "short"},
        {"surprise": "field"},
    ],
)
def test_draft_rejects_invalid_fields(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        draft(**overrides)
