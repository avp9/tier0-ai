import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import requests
from agent_harness import ALICE, ANSWERABLE, GAP, Harness
from langsmith import Client
from langsmith import utils as ls_utils
from pydantic import BaseModel
from tracing_support import CapturingSession, make_settings, make_tracing

from tier0.observability.tracing import (
    MAX_ITEMS,
    MAX_TEXT_CHARS,
    build_tracing,
    note,
    project_name,
    scrub,
    scrub_text,
    traced,
)
from tier0.rag.embeddings import FakeEmbedder
from tier0.rag.indexer import build_index
from tier0.rag.loader import load_knowledge_base
from tier0.rag.retriever import Retriever

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "data" / "fixtures"


@pytest.fixture(scope="module")
def retriever(tmp_path_factory: pytest.TempPathFactory) -> Retriever:
    index = tmp_path_factory.mktemp("trace-index") / "chroma"
    build_index(load_knowledge_base(FIXTURE_DIR), FakeEmbedder(), index)
    return Retriever.open(index, FakeEmbedder())


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The LangSmith client waits between retries of a failed upload; a test need not."""
    monkeypatch.setattr("langsmith.client.time.sleep", lambda _seconds: None)


def traced_harness(retriever: Retriever, **overrides: Any) -> tuple[Harness, CapturingSession]:
    tracing, session = make_tracing(**overrides)
    return Harness(retriever, tracing=tracing), session


def say(h: Harness, text: str, **kw: Any) -> Any:
    return h.agent.send(thread_id="t-1", requester_email=ALICE, message=text, user_id="u-1", **kw)


# --- what a trace looks like -----------------------------------------------------------


def test_a_turn_is_one_trace_named_chat_turn_whose_id_is_the_turns_id(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    turn = say(h, ANSWERABLE)
    root = session.runs()[0]
    assert root["name"] == "chat_turn" and root["id"] == turn.trace_id
    assert uuid.UUID(turn.trace_id)
    assert all(run["trace_id"] == turn.trace_id for run in session.run_updates())


def test_every_step_of_the_agent_is_part_of_the_one_trace(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    say(h, ANSWERABLE)
    names = [run["name"] for run in session.runs()]
    assert {"chat_turn", "classify", "answer", "answer_question", "knowledge_base_search"} <= set(
        names
    )
    search = next(r for r in session.runs() if r["name"] == "knowledge_base_search")
    answer_step = next(r for r in session.runs() if r["name"] == "answer_question")
    assert search["run_type"] == "retriever" and search["parent_run_id"] == answer_step["id"]


def test_the_retrieved_passages_and_scores_are_in_the_trace(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    say(h, ANSWERABLE)
    finished = next(u for u in session.run_updates() if u.get("name") == "knowledge_base_search")
    documents = finished["outputs"]["documents"]
    assert documents and {"page_content", "metadata"} <= set(documents[0])
    assert {"document_id", "title", "score"} <= set(documents[0]["metadata"])


def test_the_trace_goes_to_the_environments_own_project(retriever: Retriever) -> None:
    for env in ("local", "staging", "production"):
        h, session = traced_harness(retriever, app_env=env, llm_provider="openai")
        say(h, ANSWERABLE)
        assert {r["session_name"] for r in session.runs()} == {f"tier0-support-agent-{env}"}


def test_a_different_agent_id_changes_the_project(retriever: Retriever) -> None:
    h, session = traced_harness(retriever, langsmith_agent_id="help-desk")
    say(h, ANSWERABLE)
    assert {r["session_name"] for r in session.runs()} == {"help-desk-local"}


def test_the_conversation_and_asker_label_the_trace_so_turns_group_into_threads(
    retriever: Retriever,
) -> None:
    h, session = traced_harness(retriever)
    say(h, ANSWERABLE)
    say(h, GAP)
    roots = [r for r in session.runs() if r["name"] == "chat_turn"]
    assert [r["extra"]["metadata"]["thread_id"] for r in roots] == ["t-1", "t-1"]
    assert roots[0]["extra"]["metadata"]["user_id"] == "u-1"
    assert roots[0]["extra"]["metadata"]["environment"] == "local"
    assert set(roots[0]["tags"]) == {"tier0", "env:local"}


def test_each_turn_has_its_own_trace(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    first, second = say(h, ANSWERABLE), say(h, GAP)
    assert first.trace_id != second.trace_id
    assert {r["id"] for r in session.runs() if r["name"] == "chat_turn"} == {
        first.trace_id,
        second.trace_id,
    }


def test_a_streamed_turn_is_traced_the_same_way(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    events = list(h.agent.stream(thread_id="t-s", requester_email=ALICE, message=ANSWERABLE))
    turn: Any = events[-1]  # the finished turn comes last
    roots = [r for r in session.runs() if r["name"] == "chat_turn"]
    assert len(roots) == 1 and roots[0]["id"] == turn.trace_id
    assert any(r["name"] == "knowledge_base_search" for r in session.runs())


def test_the_semantic_cache_outcome_is_recorded_on_the_answer_step(retriever: Retriever) -> None:
    from tier0.rag.semantic_cache import InMemorySemanticCache

    tracing, session = make_tracing()
    h = Harness(retriever, cache=InMemorySemanticCache(), tracing=tracing)
    say(h, ANSWERABLE)
    say(h, ANSWERABLE)
    answers = [r for r in session.runs() if r["name"] == "answer_question"]
    assert len(answers) == 2
    outputs = [u["outputs"] for u in session.run_updates() if u.get("name") == "answer_question"]
    assert [o["cached"] for o in outputs] == [False, True]
    assert "knowledge_base_search" not in [r["name"] for r in session.runs()[-4:]]  # no search 2nd


# --- nothing personal leaves ------------------------------------------------------------


def test_emails_and_numbers_typed_by_the_employee_never_reach_langsmith(
    retriever: Retriever,
) -> None:
    h, session = traced_harness(retriever)
    say(h, f"{ANSWERABLE} Reach me on 0044 7700 900123 or at alice@buzztech.example")
    sent = session.everything_sent()
    assert "alice@buzztech" not in sent and "7700 900123" not in sent and "0044" not in sent
    assert "[email]" in sent and "[number]" in sent


def test_who_is_asking_is_removed_from_every_run(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    say(h, ANSWERABLE)
    sent = session.everything_sent()
    assert ALICE not in sent and "alice@" not in sent
    root = session.runs()[0]
    assert root["inputs"]["requester_email"] == "[removed]"


def test_the_trace_still_says_what_was_asked_and_answered(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    say(h, ANSWERABLE)
    assert ANSWERABLE in session.runs()[0]["inputs"]["user_message"]
    root_done = next(u for u in session.run_updates() if u["id"] == session.runs()[0]["id"])
    assert "Here is what the handbook says." in json.dumps(root_done["outputs"])


def test_secrets_in_a_message_are_masked(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    say(h, f"{ANSWERABLE} my key is sk-abcdefghijklmnopqrstuvwx and password: hunter2hunter2")
    sent = session.everything_sent()
    assert "sk-abcdefghijklmnop" not in sent and "hunter2hunter2" not in sent


def test_only_the_last_few_earlier_messages_are_sent_with_each_step(retriever: Retriever) -> None:
    h, session = traced_harness(retriever)
    for n in range(MAX_ITEMS + 8):
        say(h, f"{ANSWERABLE} number {n}")
    last_root = [r for r in session.runs() if r["name"] == "chat_turn"][-1]
    turns = last_root["inputs"].get("turns") or []
    assert len(turns) <= MAX_ITEMS


# --- the scrubber on its own ------------------------------------------------------------


def test_scrub_text_masks_emails_numbers_and_secrets() -> None:
    text = "mail bob@example.org, card 4111 1111 1111 1111, key sk-abcdefghijklmnopqrstuv"
    cleaned = scrub_text(text)
    assert "bob@example.org" not in cleaned and "4111" not in cleaned and "sk-abcdef" not in cleaned
    assert cleaned.startswith("mail [email], card [number]")


def test_scrub_text_keeps_ordinary_text_and_short_numbers() -> None:
    assert scrub_text("Room 12 on floor 3, 2026") == "Room 12 on floor 3, 2026"


def test_scrub_text_cuts_very_long_text() -> None:
    cleaned = scrub_text("x" * (MAX_TEXT_CHARS + 500))
    assert len(cleaned) < MAX_TEXT_CHARS + 20 and cleaned.endswith("[…]")


def test_scrub_removes_sensitive_keys_at_any_depth() -> None:
    cleaned = scrub(
        {"a": {"requester_email": "x@y.zz", "Password": "p", "keep": "v"}, "email": "q"}
    )
    assert cleaned == {"a": {"requester_email": "[removed]", "Password": "[removed]", "keep": "v"},
                       "email": "[removed]"}  # fmt: skip


def test_scrub_keeps_only_the_most_recent_items_of_a_long_list() -> None:
    cleaned = scrub(list(range(MAX_ITEMS + 10)))
    assert len(cleaned) == MAX_ITEMS and cleaned[-1] == MAX_ITEMS + 9


def test_scrub_understands_models_and_leaves_plain_values_alone() -> None:
    class Person(BaseModel):
        name: str
        mail: str

    assert scrub(Person(name="A", mail="a@b.example")) == {"name": "A", "mail": "[email]"}
    assert scrub({"n": 1, "f": 1.5, "b": True, "none": None}) == {
        "n": 1,
        "f": 1.5,
        "b": True,
        "none": None,
    }


def test_scrub_stops_at_a_sensible_depth() -> None:
    nested: Any = "deep alice@x.example"
    for _ in range(20):
        nested = {"k": nested}
    assert "alice@x" not in json.dumps(scrub(nested))


def test_project_names_follow_the_agent_address_convention() -> None:
    assert project_name("tier0-support-agent", "staging") == "tier0-support-agent-staging"


# --- off means off ----------------------------------------------------------------------


def test_with_tracing_off_nothing_is_sent_and_the_agent_works(retriever: Retriever) -> None:
    session = CapturingSession()
    h = Harness(retriever)  # no tracing
    turn = say(h, ANSWERABLE)
    assert turn.reply.endswith("Documents to Bring on Your First Day")
    assert session.calls == []
    assert uuid.UUID(turn.trace_id)  # a turn still has an id (a vote needs one)


def test_the_traced_steps_are_silent_outside_a_turn(retriever: Retriever) -> None:
    """Scripts and tests call the same code: they must never start a trace of their own."""
    tracing, session = make_tracing()  # a client exists, but no turn is running
    assert tracing is not None
    retriever.search(ANSWERABLE, k=3)
    assert session.calls == []


def test_the_traced_decorator_runs_the_function_normally_either_way() -> None:
    @traced(name="adds")
    def add(a: int, b: int) -> int:
        note(sum=a + b)  # harmless outside a trace
        return a + b

    assert add(2, 3) == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"langsmith_tracing": False},
        {"app_env": "e2e"},
        {"langsmith_api_key": None},
    ],
)
def test_tracing_is_not_built_when_it_is_off_or_unusable(overrides: dict[str, Any]) -> None:
    made: list[Any] = []

    def factory(**kwargs: Any) -> Client:
        made.append(kwargs)
        raise AssertionError("no client should be made")

    assert build_tracing(make_settings(**overrides), client_factory=factory) is None
    assert made == []  # not even a client was made


def test_a_deployment_that_asks_for_tracing_without_a_key_does_not_start() -> None:
    with pytest.raises(ValueError, match="LANGSMITH_API_KEY"):
        make_settings(app_env="production", langsmith_api_key=None, llm_provider="openai")
    make_settings(app_env="local", langsmith_api_key=None)  # local is only warned about


def test_an_agent_id_that_langsmith_would_reject_is_refused() -> None:
    for bad in ("Has Capitals", "1starts-with-digit", "under_score", ""):
        with pytest.raises(ValueError):
            make_settings(langsmith_agent_id=bad)


def test_an_environment_variable_cannot_switch_ambient_tracing_on(monkeypatch: Any) -> None:
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2"):
        monkeypatch.setenv(name, "true")  # as a .env file might
    ls_utils.get_env_var.cache_clear()  # type: ignore[attr-defined]
    assert ls_utils.tracing_is_enabled()
    build_tracing(make_settings(langsmith_tracing=False))
    assert not ls_utils.tracing_is_enabled()  # only the app's own settings decide


# --- trouble at LangSmith never reaches the employee ---------------------------------------


def test_langsmith_being_unreachable_does_not_break_a_turn(
    retriever: Retriever, no_sleep: None
) -> None:
    session = CapturingSession(fail=requests.ConnectionError("down"))
    tracing, _ = make_tracing(session)
    h = Harness(retriever, tracing=tracing)
    turn = say(h, ANSWERABLE)
    assert turn.reply.endswith("Documents to Bring on Your First Day") and session.calls


def test_langsmith_refusing_the_key_does_not_break_a_turn(
    retriever: Retriever, no_sleep: None
) -> None:
    class Refusing(CapturingSession):
        def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            super().request(method, url, *args, **kwargs)
            raise requests.HTTPError("403 Forbidden")

    tracing, session = make_tracing(Refusing())
    h = Harness(retriever, tracing=tracing)
    assert say(h, ANSWERABLE).reply and session.calls


# --- votes sent to LangSmith ----------------------------------------------------------------


def test_a_vote_is_sent_to_langsmith_against_the_turn() -> None:
    tracing, session = make_tracing()
    run_id = str(uuid.uuid4())
    tracing.submit_feedback(run_id, 1.0, "very helpful")
    feedback = [b for m, u, b in session.calls if "feedback" in u and isinstance(b, dict)]
    assert feedback
    assert any(b.get("run_id") == run_id and b.get("key") == "user_score" for b in feedback)


def test_changing_a_vote_updates_the_one_already_recorded() -> None:
    tracing, session = make_tracing()
    tracing.submit_feedback(str(uuid.uuid4()), 0.0, replace=True)
    assert any(m == "PATCH" and u.startswith("/feedback/") for m, u, _ in session.calls)
    assert not any(m == "POST" and u == "/feedback" for m, u, _ in session.calls)


def test_a_vote_is_filed_under_the_environments_project_and_the_turns_trace() -> None:
    tracing, session = make_tracing(app_env="staging", llm_provider="openai")
    run_id = str(uuid.uuid4())
    tracing.submit_feedback(run_id, 1.0)
    (sent,) = [b for m, u, b in session.calls if m == "POST" and u == "/feedback"]
    assert sent["trace_id"] == run_id and sent["run_id"] == run_id
    assert sent["address"] == "lrn:agents/tier0-support-agent/environments/staging"


def test_a_changed_or_withdrawn_vote_waits_for_the_first_one_to_land() -> None:
    """The first vote uploads in the background; changing it must not overtake it."""
    order: list[str] = []

    class Recording:
        def flush(self, *a: Any, **k: Any) -> None:
            order.append("flush")

        def create_feedback(self, *a: Any, **k: Any) -> None:
            order.append("create")

        def update_feedback(self, *a: Any, **k: Any) -> None:
            order.append("update")

        def delete_feedback(self, *a: Any, **k: Any) -> None:
            order.append("delete")

    tracing, _ = make_tracing()
    tracing._client = Recording()  # type: ignore[assignment]
    run_id = str(uuid.uuid4())
    tracing.submit_feedback(run_id, 1.0)
    tracing.submit_feedback(run_id, 0.0, replace=True)
    tracing.submit_feedback(run_id, None)
    assert order == ["create", "flush", "update", "flush", "delete"]


def test_taking_a_vote_back_deletes_it() -> None:
    tracing, session = make_tracing()
    tracing.submit_feedback(str(uuid.uuid4()), None)
    assert any(m == "DELETE" and u.startswith("/feedback/") for m, u, _ in session.calls)


def test_a_vote_comment_is_scrubbed_before_it_is_sent() -> None:
    tracing, session = make_tracing()
    tracing.submit_feedback(str(uuid.uuid4()), 0.0, "wrong, call me on 07700900123 or a@b.example")
    sent = session.everything_sent()
    assert "07700900123" not in sent and "a@b.example" not in sent


def test_a_failure_sending_a_vote_is_swallowed(no_sleep: None) -> None:
    tracing, _ = make_tracing(CapturingSession(fail=requests.ConnectionError("down")))
    tracing.submit_feedback(str(uuid.uuid4()), 1.0)  # no exception
    tracing.submit_feedback(str(uuid.uuid4()), None)  # withdrawing, also quietly
