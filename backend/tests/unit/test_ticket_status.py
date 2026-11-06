from datetime import UTC, datetime

import pytest

from tier0.tickets.errors import TicketServiceUnavailable
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.models import TicketDraft, TicketSummary
from tier0.tickets.status import (
    NO_UPDATES,
    NONE_REPLY,
    NOT_FOUND_REPLY,
    UNAVAILABLE_REPLY,
    StatusOutcome,
    TicketStatusService,
    choose_ticket,
)

ALICE, BOB = "alice@buzztech.example", "bob@buzztech.example"


class CountingAdapter(FakeTicketAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.detail_calls: list[str] = []

    def get_ticket(self, key, requester_email):  # type: ignore[no-untyped-def]  # noqa: ANN001
        self.detail_calls.append(key)
        return super().get_ticket(key, requester_email)


def add(adapter: FakeTicketAdapter, email: str, summary: str) -> str:
    draft = TicketDraft(
        requester_email=email, summary=summary, description="details", category="IT"
    )
    return adapter.create_ticket(draft).key


# AT-12: no tickets ---------------------------------------------------------------------


def test_no_tickets_is_stated_clearly_and_nothing_is_invented() -> None:
    result = TicketStatusService(FakeTicketAdapter()).lookup(ALICE)
    assert result.outcome is StatusOutcome.NONE_FOUND
    assert result.reply == NONE_REPLY and result.tickets == [] and result.details is None


def test_other_peoples_tickets_do_not_count_as_yours() -> None:
    adapter = FakeTicketAdapter()
    add(adapter, BOB, "Bob's problem")
    assert TicketStatusService(adapter).lookup(ALICE).outcome is StatusOutcome.NONE_FOUND


# AT-10: one ticket ---------------------------------------------------------------------


def test_one_ticket_shows_status_time_link_and_support_updates() -> None:
    adapter = FakeTicketAdapter()
    key = add(adapter, ALICE, "VPN down at home")
    adapter.set_status(key, "In Progress")
    adapter.add_comment(key, "Dana", "Network team is investigating.")
    result = TicketStatusService(adapter).lookup(ALICE)
    assert result.outcome is StatusOutcome.ONE
    assert key in result.reply and "VPN down at home" in result.reply
    assert "Status: In Progress." in result.reply and "Last updated" in result.reply
    assert "https://tickets.example.invalid/browse/SUP-1" in result.reply
    assert "Dana" in result.reply and "Network team is investigating." in result.reply
    assert result.details is not None and result.details.summary.key == key


def test_a_ticket_with_no_comments_says_there_are_no_updates_yet() -> None:
    adapter = FakeTicketAdapter()
    add(adapter, ALICE, "Printer offline")
    assert NO_UPDATES in TicketStatusService(adapter).lookup(ALICE).reply


def test_only_the_latest_updates_are_shown_trimmed_and_never_internal_notes() -> None:
    adapter = FakeTicketAdapter()
    key = add(adapter, ALICE, "Laptop slow")
    for number in range(5):
        adapter.add_comment(key, "Dana", f"update {number}")
    adapter.add_comment(key, "Dana", "secret internal note", internal=True)
    adapter.add_comment(key, "Dana", "long " * 200)
    reply = TicketStatusService(adapter).lookup(ALICE).reply
    assert "update 4" in reply and "update 0" not in reply and "update 2" not in reply
    assert "secret internal note" not in reply
    assert "…" in reply and max(len(line) for line in reply.splitlines()) < 400


# AT-11: several tickets ----------------------------------------------------------------


def test_several_tickets_are_listed_and_none_is_chosen_for_the_employee() -> None:
    adapter = CountingAdapter()
    first, second, third = (add(adapter, ALICE, f"Issue {n}") for n in (1, 2, 3))
    adapter.set_status(second, "Resolved")
    result = TicketStatusService(adapter).lookup(ALICE)
    assert result.outcome is StatusOutcome.MULTIPLE
    assert [t.key for t in result.tickets] == [second, third, first]  # newest update first
    assert "You have 3 tickets" in result.reply and "Which ticket" in result.reply
    assert "1. SUP-2: Issue 2 [Resolved" in result.reply
    assert result.details is None and adapter.detail_calls == []


def test_the_chosen_ticket_then_shows_its_details() -> None:
    adapter = FakeTicketAdapter()
    add(adapter, ALICE, "First")
    second = add(adapter, ALICE, "Second")
    adapter.add_comment(second, "Dana", "Fixed it.")
    result = TicketStatusService(adapter).details(second, ALICE)
    assert result.outcome is StatusOutcome.SELECTED and "Fixed it." in result.reply


# AT-13: the ticket system fails --------------------------------------------------------


def test_an_outage_is_reported_as_an_outage_not_as_no_tickets() -> None:
    adapter = FakeTicketAdapter()
    add(adapter, ALICE, "Real ticket")
    adapter.fail_next(TicketServiceUnavailable("down"))
    result = TicketStatusService(adapter).lookup(ALICE)
    assert result.outcome is StatusOutcome.UNAVAILABLE
    assert result.reply == UNAVAILABLE_REPLY and result.reply != NONE_REPLY
    assert "try again" in result.reply and result.tickets == [] and result.details is None


class FailsOnDetails(FakeTicketAdapter):
    def get_ticket(self, key, requester_email):  # type: ignore[no-untyped-def]  # noqa: ANN001
        raise TicketServiceUnavailable("down while fetching")


def test_an_outage_while_fetching_details_is_also_reported() -> None:
    adapter = FailsOnDetails()
    add(adapter, ALICE, "Only ticket")
    service = TicketStatusService(adapter)
    assert service.lookup(ALICE).outcome is StatusOutcome.UNAVAILABLE
    assert service.details("SUP-1", ALICE).outcome is StatusOutcome.UNAVAILABLE


# Access control ------------------------------------------------------------------------


def test_asking_for_someone_elses_ticket_looks_the_same_as_a_missing_one() -> None:
    adapter = FakeTicketAdapter()
    bobs = add(adapter, BOB, "Bob's private issue")
    service = TicketStatusService(adapter)
    foreign, missing = service.details(bobs, ALICE), service.details("SUP-999", ALICE)
    assert foreign.outcome is missing.outcome is StatusOutcome.NOT_FOUND
    assert foreign.reply == missing.reply == NOT_FOUND_REPLY
    assert "private" not in foreign.reply and foreign.details is None


def test_invalid_inputs_are_programming_errors_not_user_replies() -> None:
    with pytest.raises(ValueError, match="email"):
        TicketStatusService(FakeTicketAdapter()).lookup("not-an-email")


# Understanding which ticket the employee means -----------------------------------------


def option(key: str, title: str = "x") -> TicketSummary:
    return TicketSummary(
        key=key,
        title=title,
        status="Open",
        url=f"https://t/{key}",
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


OPTIONS = [option("SUP-12"), option("SUP-7"), option("SUP-3")]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SUP-7", "SUP-7"),
        ("sup-7 please", "SUP-7"),
        ("what is the status of sup-12?", "SUP-12"),
        ("2", "SUP-7"),
        ("#3", "SUP-3"),
        ("number 1", "SUP-12"),
        ("ticket 2.", "SUP-7"),
        ("the second one", "SUP-7"),
        ("The 3rd", "SUP-3"),
        ("first", "SUP-12"),
        ("SUP-7 or maybe ticket 2", "SUP-7"),
        ("sup-12, the first one", "SUP-12"),
    ],
)
def test_clear_choices_are_understood(text: str, expected: str) -> None:
    selection = choose_ticket(text, OPTIONS)
    assert selection.ticket is not None and selection.ticket.key == expected


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("SUP-99", "unknown_key"),
        ("SUP-7 and SUP-99", "unknown_key"),
        ("SUP-7 and SUP-3", "ambiguous"),
        ("SUP-12 or ticket 2", "ambiguous"),
        ("first or second", "ambiguous"),
        ("the first, no wait, ticket 3", "ambiguous"),
        ("4", "out_of_range"),
        ("0", "out_of_range"),
        ("the vpn one", "not_understood"),
        ("hello", "not_understood"),
        ("", "not_understood"),
    ],
)
def test_unclear_replies_choose_nothing_so_the_agent_can_ask_again(text: str, reason: str) -> None:
    selection = choose_ticket(text, OPTIONS)
    assert selection.ticket is None and selection.reason == reason
