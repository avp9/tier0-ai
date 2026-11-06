from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from psycopg_pool import ConnectionPool

from tier0.agent.checkpoint import make_pool
from tier0.agent.state import PendingTicket
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.postgres_store import PostgresSubmissionStore
from tier0.tickets.submission import (
    InMemorySubmissionStore,
    SubmissionStatus,
    SubmissionStore,
    SubmitOutcome,
    TicketSubmissionService,
)

ALICE = "alice@buzztech.example"


@pytest.fixture
def pool(migrated_database: str) -> Iterator[ConnectionPool]:
    pool = make_pool(migrated_database, max_size=12)
    yield pool
    pool.close()


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> SubmissionStore:
    if request.param == "memory":
        return InMemorySubmissionStore()
    return PostgresSubmissionStore(request.getfixturevalue("pool"))


def test_begin_creates_a_pending_submission_then_returns_the_same_one(
    store: SubmissionStore,
) -> None:
    first, created = store.begin("t1", ALICE, "h1")
    again, created_again = store.begin("t1", ALICE, "h1")
    assert created and not created_again and again.id == first.id
    assert first.status is SubmissionStatus.PENDING and first.ticket_key is None
    assert store.live("t1", "h1") == first and store.get(first.id) == first


def test_other_conversations_or_content_get_their_own_submission(store: SubmissionStore) -> None:
    ids = {store.begin(t, ALICE, h)[0].id for t, h in [("t1", "h1"), ("t2", "h1"), ("t1", "h2")]}
    assert len(ids) == 3


def test_a_submission_records_its_ticket_key_when_it_succeeds(store: SubmissionStore) -> None:
    row, _ = store.begin("t1", ALICE, "h1")
    store.mark_submitted(row.id, "SUP-7")
    done = store.get(row.id)
    assert (
        done is not None
        and done.status is SubmissionStatus.SUBMITTED
        and done.ticket_key == "SUP-7"
    )
    assert store.begin("t1", ALICE, "h1")[1] is False  # a submitted ticket still blocks a duplicate


@pytest.mark.parametrize("status", [SubmissionStatus.FAILED, SubmissionStatus.CANCELLED])
def test_a_failed_or_cancelled_submission_no_longer_blocks_a_new_one(
    store: SubmissionStore, status: SubmissionStatus
) -> None:
    row, _ = store.begin("t1", ALICE, "h1")
    store.mark(row.id, status)
    assert store.live("t1", "h1") is None
    fresh, created = store.begin("t1", ALICE, "h1")
    assert created and fresh.id != row.id


def test_an_unknown_outcome_keeps_blocking_so_it_gets_reconciled(store: SubmissionStore) -> None:
    row, _ = store.begin("t1", ALICE, "h1")
    store.mark(row.id, SubmissionStatus.UNKNOWN)
    again, created = store.begin("t1", ALICE, "h1")
    assert not created and again.id == row.id and again.status is SubmissionStatus.UNKNOWN


def test_marking_submitted_requires_the_key_and_unknown_ids_are_absent(
    store: SubmissionStore,
) -> None:
    row, _ = store.begin("t1", ALICE, "h1")
    with pytest.raises(ValueError, match="mark_submitted"):
        store.mark(row.id, SubmissionStatus.SUBMITTED)
    assert store.get("00000000-0000-0000-0000-000000000000") is None
    assert store.live("nope", "nope") is None


def test_emails_are_stored_lowercase(store: SubmissionStore) -> None:
    row, _ = store.begin("t1", "Alice@Buzztech.Example", "h1")
    assert row.requester_email == ALICE


# --- Postgres-only: concurrency and end to end ----------------------------------------


def test_simultaneous_submissions_converge_on_one_live_row(pool: ConnectionPool) -> None:
    store = PostgresSubmissionStore(pool)
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda _: store.begin("t1", ALICE, "h1"), range(10)))
    assert len({row.id for row, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_ten_threads_submitting_the_same_ticket_create_exactly_one(pool: ConnectionPool) -> None:
    adapter = FakeTicketAdapter()
    service = TicketSubmissionService(adapter, PostgresSubmissionStore(pool))
    ticket = PendingTicket(summary="VPN down", description="Cannot connect.", category="IT")

    def attempt(_: int) -> SubmitOutcome:
        return service.submit(thread_id="t1", requester_email=ALICE, ticket=ticket).outcome

    with ThreadPoolExecutor(max_workers=10) as executor:
        outcomes = list(executor.map(attempt, range(10)))

    assert adapter.create_calls == 1 and len(adapter.tickets) == 1
    assert outcomes.count(SubmitOutcome.CREATED) == 1
    assert set(outcomes) <= {
        SubmitOutcome.CREATED,
        SubmitOutcome.IN_PROGRESS,
        SubmitOutcome.ALREADY_CREATED,
    }


def test_the_service_with_the_real_database_blocks_a_repeat(pool: ConnectionPool) -> None:
    adapter = FakeTicketAdapter()
    service = TicketSubmissionService(adapter, PostgresSubmissionStore(pool))
    ticket = PendingTicket(summary="VPN down", description="Cannot connect.", category="IT")
    first = service.submit(thread_id="t1", requester_email=ALICE, ticket=ticket)
    second = service.submit(thread_id="t1", requester_email=ALICE, ticket=ticket)
    assert first.outcome is SubmitOutcome.CREATED
    assert second.outcome is SubmitOutcome.ALREADY_CREATED and second.ticket_key == first.ticket_key
    assert len(adapter.tickets) == 1
