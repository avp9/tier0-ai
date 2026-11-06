import pytest

from tier0.tickets.errors import TicketNotFound, TicketOutcomeUnknown, TicketServiceUnavailable
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.models import TicketDraft

ALICE, BOB = "alice@buzztech.example", "bob@buzztech.example"


def draft(email: str = ALICE, **overrides: object) -> TicketDraft:
    base: dict[str, object] = {
        "requester_email": email,
        "summary": "VPN down",
        "description": "Cannot connect.",
        "category": "IT",
    }
    return TicketDraft.model_validate({**base, **overrides})


def test_create_returns_sequential_keys_and_urls() -> None:
    fake = FakeTicketAdapter()
    first, second = fake.create_ticket(draft()), fake.create_ticket(draft())
    assert (first.key, second.key) == ("SUP-1", "SUP-2")
    assert first.url == "https://tickets.example.invalid/browse/SUP-1"


def test_tickets_are_visible_only_to_their_requester_newest_first() -> None:
    fake = FakeTicketAdapter()
    fake.create_ticket(draft(ALICE, summary="first"))
    fake.create_ticket(draft(BOB, summary="bobs"))
    fake.create_ticket(draft(ALICE, summary="second"))
    assert [t.title for t in fake.find_tickets(ALICE)] == ["second", "first"]
    assert [t.title for t in fake.find_tickets(BOB)] == ["bobs"]
    assert fake.find_tickets("carol@buzztech.example") == []


def test_get_ticket_hides_other_peoples_tickets_and_internal_comments() -> None:
    fake = FakeTicketAdapter()
    key = fake.create_ticket(draft(ALICE)).key
    fake.add_comment(key, "Dana", "Investigating.")
    fake.add_comment(key, "Dana", "internal note", internal=True)
    fake.set_status(key, "In Progress")
    details = fake.get_ticket(key, ALICE)
    assert details.summary.status == "In Progress"
    assert [c.body for c in details.comments] == ["Investigating."]
    with pytest.raises(TicketNotFound):
        fake.get_ticket(key, BOB)


def test_failures_are_one_shot_and_a_failed_create_stores_nothing() -> None:
    fake = FakeTicketAdapter()
    fake.fail_next(TicketServiceUnavailable("down"))
    with pytest.raises(TicketServiceUnavailable):
        fake.create_ticket(draft())
    assert fake.tickets == []
    assert fake.create_ticket(draft()).key == "SUP-1"


def test_a_lost_response_creates_the_ticket_but_reports_an_unknown_outcome() -> None:
    fake = FakeTicketAdapter()
    fake.lose_next_create_response()
    with pytest.raises(TicketOutcomeUnknown):
        fake.create_ticket(draft(submission_id="3f2b8c1e-aaaa-bbbb"))
    found = fake.find_by_submission_id("3f2b8c1e-aaaa-bbbb", ALICE)
    assert found is not None and found.key == "SUP-1"
    assert fake.find_by_submission_id("3f2b8c1e-aaaa-bbbb", BOB) is None


def test_the_fake_does_not_deduplicate_like_the_real_platform() -> None:
    fake = FakeTicketAdapter()
    fake.create_ticket(draft(submission_id="3f2b8c1e-aaaa-bbbb"))
    fake.create_ticket(draft(submission_id="3f2b8c1e-aaaa-bbbb"))
    assert len(fake.tickets) == 2 and fake.create_calls == 2


def test_inputs_are_validated_like_the_real_adapter() -> None:
    fake = FakeTicketAdapter()
    with pytest.raises(ValueError, match="email"):
        fake.find_tickets("not-an-email")
    with pytest.raises(ValueError, match="key"):
        fake.get_ticket("bad key", ALICE)
