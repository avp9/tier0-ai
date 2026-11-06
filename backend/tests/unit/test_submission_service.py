from datetime import UTC, datetime, timedelta

import pytest

from tier0.agent.state import PendingTicket
from tier0.tickets.errors import (
    TicketOutcomeUnknown,
    TicketRejected,
    TicketServiceUnavailable,
)
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.models import TicketDraft
from tier0.tickets.submission import (
    InMemorySubmissionStore,
    SubmissionStatus,
    SubmitOutcome,
    TicketSubmissionService,
    payload_hash,
)

ALICE = "alice@buzztech.example"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


CLOCK = FakeClock()


@pytest.fixture(autouse=True)
def _reset_clock() -> None:
    CLOCK.now = datetime(2026, 1, 1, tzinfo=UTC)


def ticket(**overrides: object) -> PendingTicket:
    base: dict[str, object] = {
        "summary": "VPN down at home",
        "description": "Cannot connect since Monday.",
        "category": "IT",
        "priority": "High",
    }
    return PendingTicket.model_validate({**base, **overrides})


def setup(adapter: FakeTicketAdapter | None = None):  # type: ignore[no-untyped-def]
    adapter = adapter or FakeTicketAdapter()
    store = InMemorySubmissionStore(CLOCK)
    return adapter, store, TicketSubmissionService(adapter, store, CLOCK)


def submit(service: TicketSubmissionService, t: PendingTicket | None = None, thread: str = "t1"):  # type: ignore[no-untyped-def]
    return service.submit(thread_id=thread, requester_email=ALICE, ticket=t or ticket())


# --- the normal path ------------------------------------------------------------------


def test_a_complete_ticket_is_created_once_and_reported_with_its_key_and_link() -> None:
    adapter, store, service = setup()
    result = submit(service)
    assert result.outcome is SubmitOutcome.CREATED and result.has_ticket
    assert (
        result.ticket_key == "SUP-1"
        and result.ticket_url == "https://tickets.example.invalid/browse/SUP-1"
    )
    assert (
        "SUP-1" in result.reply and "https://tickets.example.invalid/browse/SUP-1" in result.reply
    )
    assert adapter.create_calls == 1 and len(adapter.tickets) == 1
    (row,) = store.rows.values()
    assert row.status is SubmissionStatus.SUBMITTED and row.ticket_key == "SUP-1"


def test_the_platform_receives_the_submission_id_and_a_lowercase_requester() -> None:
    adapter, store, service = setup()
    service.submit(thread_id="t1", requester_email="  Alice@Buzztech.Example ", ticket=ticket())
    sent: TicketDraft = adapter.tickets[0].draft
    (row,) = store.rows.values()
    assert sent.submission_id == row.id and sent.requester_email == ALICE
    assert (sent.summary, sent.category, sent.priority) == ("VPN down at home", "IT", "High")


# --- AT-18: repeating a submission ----------------------------------------------------


def test_submitting_the_same_ticket_again_never_creates_a_second_one() -> None:
    adapter, _, service = setup()
    first, second, third = submit(service), submit(service), submit(service)
    assert first.outcome is SubmitOutcome.CREATED
    assert second.outcome is third.outcome is SubmitOutcome.ALREADY_CREATED
    assert second.ticket_key == "SUP-1" and "haven't created a second one" in second.reply
    assert adapter.create_calls == 1 and len(adapter.tickets) == 1


def test_different_content_or_a_different_conversation_is_a_different_ticket() -> None:
    adapter, _, service = setup()
    submit(service)
    submit(service, ticket(summary="Printer offline"))
    submit(service, thread="t2")
    assert len(adapter.tickets) == 3


def test_email_capitalisation_does_not_defeat_duplicate_protection() -> None:
    adapter, _, service = setup()
    service.submit(thread_id="t1", requester_email="alice@buzztech.example", ticket=ticket())
    service.submit(thread_id="t1", requester_email="ALICE@Buzztech.example", ticket=ticket())
    assert len(adapter.tickets) == 1


def test_a_lost_response_is_resolved_by_looking_the_ticket_up_not_by_creating_another() -> None:
    adapter, store, service = setup()
    adapter.lose_next_create_response()  # the ticket is created, but the answer never arrives
    result = submit(service)
    assert result.outcome is SubmitOutcome.CREATED and result.ticket_key == "SUP-1"
    assert adapter.create_calls == 1 and len(adapter.tickets) == 1
    assert next(iter(store.rows.values())).status is SubmissionStatus.SUBMITTED


class LosesRequest(FakeTicketAdapter):
    """Times out without the ticket ever being created."""

    def __init__(self) -> None:
        super().__init__()
        self.lose_requests = 1

    def create_ticket(self, draft: TicketDraft):  # type: ignore[no-untyped-def]
        if self.lose_requests:
            self.lose_requests -= 1
            self.create_calls += 1
            raise TicketOutcomeUnknown("timed out")
        return super().create_ticket(draft)


def test_an_unconfirmed_request_stays_unknown_and_a_retry_checks_first() -> None:
    adapter = LosesRequest()
    _, store, service = setup(adapter)
    first = submit(service)
    assert first.outcome is SubmitOutcome.UNKNOWN and first.retryable and not first.has_ticket
    assert "won't create a duplicate" in first.reply and adapter.tickets == []
    assert next(iter(store.rows.values())).status is SubmissionStatus.UNKNOWN

    second = submit(service)  # the employee says yes again
    assert second.outcome is SubmitOutcome.CREATED and len(adapter.tickets) == 1
    assert len(store.rows) == 1  # the same submission was reused


def test_a_crash_after_the_ticket_was_created_is_repaired_on_the_next_attempt() -> None:
    adapter, store, service = setup()
    row, _ = store.begin("t1", ALICE, payload_hash(ALICE, ticket()))
    adapter.create_ticket(  # Jira got it, then the process died before recording the result
        TicketDraft(
            requester_email=ALICE,
            summary="VPN down at home",
            description="Cannot connect since Monday.",
            category="IT",
            priority="High",
            submission_id=row.id,
        )
    )
    CLOCK.advance(120)  # long enough to be sure the first attempt is dead, not just slow
    result = submit(service)
    assert result.outcome is SubmitOutcome.ALREADY_CREATED and result.ticket_key == "SUP-1"
    assert len(adapter.tickets) == 1
    assert store.rows[row.id].status is SubmissionStatus.SUBMITTED


def test_if_the_lookup_itself_fails_nothing_is_created() -> None:
    adapter, store, service = setup()
    store.begin("t1", ALICE, payload_hash(ALICE, ticket()))  # an earlier attempt, outcome unclear
    CLOCK.advance(120)
    adapter.fail_next(TicketServiceUnavailable("down"))
    result = submit(service)
    assert result.outcome is SubmitOutcome.UNAVAILABLE and adapter.create_calls == 0


# --- AT-13: failures never look like success ------------------------------------------


def test_an_unreachable_platform_creates_nothing_and_allows_a_clean_retry() -> None:
    adapter, store, service = setup()
    adapter.fail_next(TicketServiceUnavailable("down"))
    failed = submit(service)
    assert (
        failed.outcome is SubmitOutcome.UNAVAILABLE and failed.retryable and not failed.has_ticket
    )
    assert "Nothing was created" in failed.reply and failed.ticket_key is None
    assert adapter.tickets == []
    assert next(iter(store.rows.values())).status is SubmissionStatus.FAILED

    retried = submit(service)
    assert retried.outcome is SubmitOutcome.CREATED and len(adapter.tickets) == 1


def test_a_rejected_ticket_is_reported_without_platform_detail_and_is_not_retryable() -> None:
    adapter, store, service = setup()
    adapter.fail_next(TicketRejected("Field 'customfield_10062' cannot be set"))
    result = submit(service)
    assert result.outcome is SubmitOutcome.REJECTED and not result.retryable
    assert "customfield" not in result.reply and adapter.tickets == []
    assert next(iter(store.rows.values())).status is SubmissionStatus.FAILED


# --- guards ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "incomplete",
    [{"summary": None}, {"description": None}, {"description": "  "}, {"category": None}],
)
def test_an_incomplete_ticket_is_never_submitted(incomplete: dict[str, object]) -> None:
    adapter, store, service = setup()
    with pytest.raises(ValueError, match="summary, description and category"):
        submit(service, ticket(**incomplete))
    assert adapter.create_calls == 0 and store.rows == {}


def test_the_payload_hash_changes_with_every_field_and_ignores_padding() -> None:
    base = payload_hash(ALICE, ticket())
    assert base == payload_hash("  ALICE@buzztech.example ", ticket(summary="  VPN down at home "))
    for change in ({"summary": "x"}, {"description": "x"}, {"category": "HR"}, {"priority": "Low"}):
        assert payload_hash(ALICE, ticket(**change)) != base
    assert payload_hash("bob@buzztech.example", ticket()) != base


# --- a double click must not create two tickets ---------------------------------------


def test_a_second_request_while_the_first_is_still_in_flight_does_not_create_a_ticket() -> None:
    adapter, store, service = setup()
    store.begin("t1", ALICE, payload_hash(ALICE, ticket()))  # the first request is mid-creation
    CLOCK.advance(5)
    result = submit(service)
    assert (
        result.outcome is SubmitOutcome.IN_PROGRESS and result.retryable and not result.has_ticket
    )
    assert "already creating" in result.reply
    assert adapter.create_calls == 0 and adapter.tickets == []


def test_an_in_flight_attempt_that_goes_quiet_is_eventually_treated_as_dead() -> None:
    adapter, store, service = setup()
    store.begin("t1", ALICE, payload_hash(ALICE, ticket()))
    CLOCK.advance(29)
    assert submit(service).outcome is SubmitOutcome.IN_PROGRESS
    CLOCK.advance(2)
    assert submit(service).outcome is SubmitOutcome.CREATED  # nothing found in Jira, so create
    assert len(adapter.tickets) == 1


def test_an_unknown_outcome_is_reconciled_immediately_without_waiting() -> None:
    adapter = LosesRequest()
    _, store, service = setup(adapter)
    assert submit(service).outcome is SubmitOutcome.UNKNOWN
    CLOCK.advance(1)  # no need to wait: an unknown outcome is not an attempt in flight
    assert submit(service).outcome is SubmitOutcome.CREATED
