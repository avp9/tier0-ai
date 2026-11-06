"""What happens when providers fail or answer badly: nothing may crash or be invented."""

from types import SimpleNamespace
from typing import Any

import pytest
import requests
from jira.exceptions import JIRAError
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError
from test_jira_adapter import CONFIG, EMAIL, FakeJira, adapter_with, draft, issue
from test_openai_chat import StubCompletions, model_with

from tier0.llm.base import LLMError
from tier0.llm.fake import FakeChatModel
from tier0.rag.answerability import AnswerabilityConfig, SufficiencyVerdict
from tier0.rag.answering import AnswerService, Outcome
from tier0.rag.retriever import Hit
from tier0.tickets.errors import (
    TicketOutcomeUnknown,
    TicketRejected,
    TicketServiceUnavailable,
)

# --- OpenAI ---------------------------------------------------------------------------

_REQUEST = SimpleNamespace(method="POST", url="https://api.openai.example")
_RESPONSE = SimpleNamespace(status_code=429, headers={}, request=_REQUEST)


@pytest.mark.parametrize(
    "error",
    [
        APIConnectionError(request=_REQUEST),  # type: ignore[arg-type]
        APITimeoutError(request=_REQUEST),  # type: ignore[arg-type]
        RateLimitError("slow down", response=_RESPONSE, body=None),  # type: ignore[arg-type]
        AuthenticationError("bad key", response=_RESPONSE, body=None),  # type: ignore[arg-type]
    ],
)
def test_every_kind_of_openai_failure_becomes_an_llm_error(error: Exception) -> None:
    with pytest.raises(LLMError):
        model_with(StubCompletions(error=error)).complete("s", "u")


@pytest.mark.parametrize("content", [None, ""])
def test_an_empty_openai_reply_is_an_llm_error_not_an_answer(content: str | None) -> None:
    with pytest.raises(LLMError):
        model_with(StubCompletions(content=content)).complete("s", "u")


def test_openai_returning_no_choices_is_an_llm_error() -> None:
    model = model_with(StubCompletions())
    model._client.chat.completions.create = lambda **_: SimpleNamespace(choices=[])  # type: ignore[method-assign,assignment]
    with pytest.raises(LLMError, match="no choices"):
        model.complete("s", "u")


# --- the answer service ---------------------------------------------------------------


class StubRetriever:
    def __init__(self, hits: list[Hit]) -> None:
        self.hits = hits

    def search(self, query: str, k: int = 5) -> list[Hit]:
        return self.hits


def hit(document_id: str = "doc-1", score: float = 0.8, **extra: str) -> Hit:
    return Hit(
        document_id=document_id,
        title=extra.get("title", "Reset a password"),
        category="IT",
        content=extra.get("content", "Use the self-service portal."),
        score=score,
        source="kb",
    )


def service(llm: FakeChatModel, hits: list[Hit] | None = None) -> AnswerService:
    retriever: Any = StubRetriever(hits if hits is not None else [hit()])
    return AnswerService(retriever, llm, AnswerabilityConfig(score_threshold=0.3))


def verdict(**kwargs: Any) -> str:
    return SufficiencyVerdict(
        **{"sufficient": True, "supporting_ids": ["doc-1"], **kwargs}
    ).model_dump_json()


def test_a_blank_answer_is_never_shown_as_an_answer() -> None:
    def respond(system: str, user: str, json_mode: bool) -> str:
        return verdict() if json_mode else "   \n  "

    result = service(FakeChatModel(respond)).answer("how do I reset my password?")
    assert result.outcome is Outcome.UNAVAILABLE
    assert "Sources:" not in result.reply


@pytest.mark.parametrize("fail_on_json", [True, False])
def test_an_outage_at_either_model_call_offers_a_ticket(fail_on_json: bool) -> None:
    def respond(system: str, user: str, json_mode: bool) -> str:
        if json_mode == fail_on_json:
            raise LLMError("down")
        return verdict() if json_mode else "An answer."

    result = service(FakeChatModel(respond)).answer("q")
    assert result.outcome is Outcome.UNAVAILABLE and result.offers_ticket


@pytest.mark.parametrize(
    "garbage",
    ["", "not json", "{", '{"sufficient": "maybe"}', '{"sufficient": null}', "[]", "{} {}", "null"],
)
def test_unparseable_sufficiency_replies_mean_no_answer_not_a_guess(garbage: str) -> None:
    result = service(FakeChatModel(lambda s, u, j: garbage)).answer("q")
    assert result.outcome is Outcome.KNOWLEDGE_GAP
    assert result.decision is not None and result.decision.reason == "llm_unparseable"


def test_the_model_cannot_cite_documents_it_was_not_given() -> None:
    def respond(system: str, user: str, json_mode: bool) -> str:
        if json_mode:
            return verdict(supporting_ids=["secret-doc", "doc-1", "../../etc/passwd"])
        return "Answer."

    result = service(FakeChatModel(respond)).answer("q")
    assert [s.document_id for s in result.sources] == ["doc-1"]


def test_a_model_claiming_sufficiency_with_no_citation_still_cites_retrieved_text() -> None:
    def respond(system: str, user: str, json_mode: bool) -> str:
        return verdict(supporting_ids=[]) if json_mode else "Answer."

    assert [s.document_id for s in service(FakeChatModel(respond)).answer("q").sources] == ["doc-1"]


# --- Jira -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (requests.exceptions.ChunkedEncodingError("cut off"), TicketOutcomeUnknown),
        (requests.exceptions.ContentDecodingError("bad body"), TicketOutcomeUnknown),
        (requests.exceptions.SSLError("handshake"), TicketServiceUnavailable),
        (JIRAError(status_code=None, text="no response read"), TicketOutcomeUnknown),
        (JIRAError(status_code=409, text="conflict"), TicketRejected),
    ],
)
def test_more_write_failures_are_classified_by_what_may_have_happened(
    error: Exception, expected: type[Exception]
) -> None:
    client = FakeJira()
    client.error = error
    with pytest.raises(expected):
        adapter_with(client).create_ticket(draft())


def test_a_success_reply_without_a_ticket_key_is_an_unknown_outcome() -> None:
    client = FakeJira()
    client.create_issue = lambda fields: SimpleNamespace()  # type: ignore[method-assign]
    with pytest.raises(TicketOutcomeUnknown):
        adapter_with(client).create_ticket(draft())
    client.create_issue = lambda fields: SimpleNamespace(key=None)  # type: ignore[method-assign]
    with pytest.raises(TicketOutcomeUnknown):
        adapter_with(client).create_ticket(draft())


@pytest.mark.parametrize(
    "bad_issue",
    [
        SimpleNamespace(key="SUP-1", raw=None),
        SimpleNamespace(key="SUP-1", raw={"fields": "text"}),
        SimpleNamespace(key="SUP-1"),
    ],
)
def test_malformed_search_results_are_an_outage_not_a_crash(bad_issue: Any) -> None:
    with pytest.raises(TicketServiceUnavailable):
        adapter_with(FakeJira(issues=[bad_issue])).find_tickets(EMAIL)


def test_null_fields_and_odd_labels_are_skipped_safely() -> None:
    odd = SimpleNamespace(key="SUP-2", raw={"fields": None})
    string_labels = issue("SUP-3", labels="tier0-env:staging")  # type: ignore[arg-type]
    good = issue("SUP-4")
    found = adapter_with(FakeJira(issues=[odd, string_labels, good])).find_tickets(EMAIL)
    assert [t.key for t in found] == ["SUP-4"]


def test_malformed_comments_are_an_outage_not_a_crash() -> None:
    client = FakeJira(issues=[issue("SUP-5")], comments=[SimpleNamespace(raw=None)])
    with pytest.raises(TicketServiceUnavailable):
        adapter_with(client).get_ticket("SUP-5", EMAIL)


def test_the_jira_token_never_reaches_an_error_message() -> None:
    error = classify_secret()
    assert CONFIG.api_token not in str(error)


def classify_secret() -> Exception:
    from tier0.tickets.jira_adapter import classify_error

    return classify_error(JIRAError(status_code=401, text=f"bad {CONFIG.api_token}"), write=True)
