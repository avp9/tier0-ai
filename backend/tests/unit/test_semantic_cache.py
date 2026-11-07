import math
import threading
from typing import Any

import pytest

from tier0.llm.base import LLMError
from tier0.llm.fake import FakeChatModel
from tier0.rag.answerability import AnswerabilityConfig, SufficiencyVerdict
from tier0.rag.answering import ANSWER_SYSTEM, AnswerService, Outcome
from tier0.rag.retriever import Hit
from tier0.rag.semantic_cache import (
    MAX_QUESTION_CHARS,
    CachedAnswer,
    InMemorySemanticCache,
    cache_namespace,
    cacheable,
    normalize_question,
)

# --- which questions may be kept --------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "How do I reset my password?",
        "What is the hotel limit for business travel?",
        "Which floor is the printer on? Room 4021",
        "x" * MAX_QUESTION_CHARS,
    ],
)
def test_ordinary_questions_may_be_cached(question: str) -> None:
    assert cacheable(question)


@pytest.mark.parametrize(
    "question",
    [
        "",
        "   ",
        "x" * (MAX_QUESTION_CHARS + 1),
        "My email is alice@buzztech.example, can you reset my password?",
        "Why was alice@buzztech.example locked out",
        "Call me on 0044 7700 900123 about my laptop",
        "My card 4111 1111 1111 1111 was charged twice",
        "Employee id 123456789 cannot log in",
    ],
)
def test_questions_with_personal_details_or_that_are_too_long_are_never_cached(
    question: str,
) -> None:
    assert not cacheable(question)


def test_a_few_digits_are_not_personal() -> None:
    assert cacheable("Meeting room 12 on floor 3 is booked until 2026")


# --- recognising the same question ------------------------------------------------------


@pytest.mark.parametrize(
    "variant",
    [
        "how do i reset my password",
        "How do I reset my password?",
        "how do i reset my password?!",
        "  HOW   DO I RESET MY PASSWORD ? ",
        "How do I reset my password...",
        "How do I reset my\tpassword",
    ],
)
def test_capitals_punctuation_and_spacing_do_not_make_a_different_question(variant: str) -> None:
    assert normalize_question(variant) == "how do i reset my password"


def test_a_different_wording_is_a_different_question() -> None:
    assert normalize_question("how can I reset my password") != normalize_question(
        "how do I reset my password"
    )


def test_accents_and_other_scripts_are_kept() -> None:
    assert normalize_question("Où est la Salle 4 ?") == "où est la salle 4"
    assert normalize_question("パスワード？") == "パスワード"


def test_a_question_of_only_punctuation_normalises_to_nothing() -> None:
    assert normalize_question("?!...") == ""


def test_the_in_memory_cache_finds_the_exact_question_without_a_vector() -> None:
    cache = InMemorySemanticCache()
    cache.store("How do I reset my password?", unit(1, 0, 0), ANSWER)
    assert cache.lookup_exact("how do i reset my password!!") == ANSWER
    assert cache.lookup_exact("how can I reset my password") is None


def test_the_exact_match_expires_too() -> None:
    now = [10.0]
    cache = InMemorySemanticCache(ttl_seconds=5, clock=lambda: now[0])
    cache.store("q one", unit(1, 0, 0), ANSWER)
    now[0] += 6
    assert cache.lookup_exact("q one") is None


# --- the namespace --------------------------------------------------------------------


def test_the_namespace_changes_when_anything_it_covers_changes() -> None:
    base = ("index-a", "model", "prompt", 0.3, 5)
    assert cache_namespace(*base) == cache_namespace(*base)
    for position, other in enumerate(["index-b", "model-2", "prompt 2", 0.31, 6]):
        changed = list(base)
        changed[position] = other
        assert cache_namespace(*changed) != cache_namespace(*base), position


def test_the_namespace_is_short_and_safe_in_a_key() -> None:
    assert len(cache_namespace("x")) == 12 and cache_namespace("x").isalnum()


# --- the in-memory cache ----------------------------------------------------------------


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


ANSWER = CachedAnswer("Use the portal.\n\nSources: Reset", [{"document_id": "d1"}])


def test_an_identical_vector_gets_the_stored_answer() -> None:
    cache = InMemorySemanticCache(min_similarity=0.95)
    cache.store("how do I reset my password", unit(1, 0, 0), ANSWER)
    assert cache.lookup(unit(1, 0, 0)) == ANSWER


def test_a_close_enough_vector_gets_it_and_a_further_one_does_not() -> None:
    cache = InMemorySemanticCache(min_similarity=0.95)
    cache.store("q", unit(1, 0, 0), ANSWER)
    assert cache.lookup(unit(1, 0.2, 0)) == ANSWER  # similarity about 0.98
    assert cache.lookup(unit(1, 0.5, 0)) is None  # about 0.89


def test_the_best_of_several_stored_questions_wins() -> None:
    cache = InMemorySemanticCache(min_similarity=0.8)
    other = CachedAnswer("other", [])
    cache.store("a", unit(1, 0.3, 0), other)
    cache.store("b", unit(1, 0.05, 0), ANSWER)
    assert cache.lookup(unit(1, 0, 0)) == ANSWER


def test_an_empty_cache_has_nothing() -> None:
    assert InMemorySemanticCache().lookup(unit(1, 0, 0)) is None


def test_entries_expire() -> None:
    now = [100.0]
    cache = InMemorySemanticCache(ttl_seconds=60, clock=lambda: now[0])
    cache.store("q", unit(1, 0, 0), ANSWER)
    now[0] += 59
    assert cache.lookup(unit(1, 0, 0)) == ANSWER
    now[0] += 2
    assert cache.lookup(unit(1, 0, 0)) is None


def test_clearing_forgets_everything() -> None:
    cache = InMemorySemanticCache()
    cache.store("q", unit(1, 0, 0), ANSWER)
    cache.clear()
    assert cache.lookup(unit(1, 0, 0)) is None and cache.lookup_exact("q") is None


def test_it_is_safe_to_use_from_several_threads() -> None:
    cache = InMemorySemanticCache()
    errors: list[Exception] = []

    def work(n: int) -> None:
        try:
            for i in range(50):
                cache.store(f"q{n}-{i}", unit(1, n, i + 1), ANSWER)
                cache.lookup(unit(1, n, i + 1))
        except Exception as error:  # pragma: no cover
            errors.append(error)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# --- the answer service with a cache -----------------------------------------------------

VECTORS: dict[str, list[float]] = {
    "How do I reset my password?": unit(1, 0, 0, 0),
    "how can I reset my password": unit(1, 0.1, 0, 0),  # means the same
    "How do I reset my VPN token?": unit(1, 1.2, 0, 0),  # a different question
    "Does BuzzTech pay for gyms?": unit(0, 0, 1, 0),
    "what about from home": unit(0, 0, 0, 1),
}


def make_hit(document_id: str = "doc-1") -> Hit:
    return Hit(
        document_id=document_id,
        title="Reset a password",
        category="IT",
        source="kb",
        content="Use the self-service portal.",
        score=0.8,
    )


class CountingRetriever:
    def __init__(self) -> None:
        self.embeds = 0
        self.searches: list[list[float] | None] = []

    def embed(self, query: str) -> list[float]:
        self.embeds += 1
        return VECTORS.get(query, unit(0.3, 0.3, 0.3, 0.3))

    def search(self, query: str, k: int = 5, *, vector: Any = None) -> list[Hit]:
        self.searches.append(list(vector) if vector is not None else None)
        return [make_hit()]


def verdict(sufficient: bool = True) -> str:
    return SufficiencyVerdict(
        sufficient=sufficient, reason="r", supporting_ids=["doc-1"] if sufficient else []
    ).model_dump_json()


class Setup:
    def __init__(self, *, sufficient: bool = True, answer: str = "Use the portal.") -> None:
        self.sufficient, self.answer, self.fail = sufficient, answer, False
        self.chat = FakeChatModel(self._respond)
        self.retriever = CountingRetriever()
        self.cache = InMemorySemanticCache(min_similarity=0.95)
        self.service = AnswerService(
            self.retriever,  # type: ignore[arg-type]
            self.chat,
            AnswerabilityConfig(score_threshold=0.3),
            cache=self.cache,
        )

    def _respond(self, system: str, user: str, json_mode: bool) -> str:
        if self.fail:
            raise LLMError("down")
        if json_mode:
            return verdict(self.sufficient)
        assert system == ANSWER_SYSTEM
        return self.answer


def test_the_first_time_a_question_is_answered_normally_and_remembered() -> None:
    s = Setup()
    first = s.service.answer("How do I reset my password?")
    assert first.outcome is Outcome.ANSWERED and not first.cached
    assert first.reply.startswith("Use the portal.") and len(s.chat.calls) == 2
    assert len(s.retriever.searches) == 1


def test_the_same_question_again_is_answered_from_the_cache_with_no_search_or_model_call() -> None:
    s = Setup()
    first = s.service.answer("How do I reset my password?")
    second = s.service.answer("How do I reset my password?")
    assert second.cached and second.outcome is Outcome.ANSWERED
    assert second.reply == first.reply and second.sources == first.sources
    assert len(s.retriever.searches) == 1 and len(s.chat.calls) == 2  # nothing more was spent


def test_a_question_that_means_the_same_gets_the_stored_answer() -> None:
    s = Setup()
    first = s.service.answer("How do I reset my password?")
    again = s.service.answer("how can I reset my password")
    assert again.cached and again.reply == first.reply
    assert len(s.chat.calls) == 2


def test_a_different_question_is_not_answered_with_someone_elses_answer() -> None:
    s = Setup()
    s.service.answer("How do I reset my password?")
    other = s.service.answer("How do I reset my VPN token?")
    assert not other.cached and len(s.chat.calls) == 4 and len(s.retriever.searches) == 2


def test_the_question_is_embedded_once_and_the_same_vector_is_used_to_search() -> None:
    s = Setup()
    s.service.answer("How do I reset my password?")
    assert s.retriever.embeds == 1
    assert s.retriever.searches == [VECTORS["How do I reset my password?"]]


def test_the_cached_answer_keeps_its_sources_and_the_sources_line() -> None:
    s = Setup()
    s.service.answer("How do I reset my password?")
    reused = s.service.answer("How do I reset my password?")
    assert [src.document_id for src in reused.sources] == ["doc-1"]
    assert reused.reply.endswith("Sources: Reset a password")
    assert reused.decision is None and reused.retrieved_ids == []


def test_the_very_same_question_is_not_even_embedded_again() -> None:
    s = Setup()
    s.service.answer("How do I reset my password?")
    assert s.retriever.embeds == 1
    for variant in ("How do I reset my password?", "how do i reset my password?!"):
        again = s.service.answer(variant)
        assert again.cached
    assert s.retriever.embeds == 1  # three repeats, and no embedding call for any of them


def test_a_reworded_question_is_embedded_once_then_found_by_meaning() -> None:
    s = Setup()
    s.service.answer("How do I reset my password?")
    again = s.service.answer("how can I reset my password")
    assert again.cached and s.retriever.embeds == 2
    assert len(s.retriever.searches) == 1  # no second search either


def test_a_cached_answer_arrives_whole_with_nothing_streamed() -> None:
    s = Setup()
    s.service.answer("How do I reset my password?")
    pieces: list[str] = []
    s.service.answer("How do I reset my password?", on_token=pieces.append)
    assert pieces == []


def test_a_knowledge_gap_is_never_cached() -> None:
    s = Setup(sufficient=False)
    assert s.service.answer("Does BuzzTech pay for gyms?").outcome is Outcome.KNOWLEDGE_GAP
    again = s.service.answer("Does BuzzTech pay for gyms?")
    assert again.outcome is Outcome.KNOWLEDGE_GAP and not again.cached
    assert len(s.retriever.searches) == 2  # it was looked up afresh, so a new article can help


def test_an_outage_is_never_cached() -> None:
    s = Setup()
    s.fail = True
    assert s.service.answer("How do I reset my password?").outcome is Outcome.UNAVAILABLE
    s.fail = False
    recovered = s.service.answer("How do I reset my password?")
    assert recovered.outcome is Outcome.ANSWERED and not recovered.cached


def test_a_blank_answer_is_never_cached() -> None:
    s = Setup(answer="   ")
    assert s.service.answer("How do I reset my password?").outcome is Outcome.UNAVAILABLE
    s.answer = "Use the portal."
    assert not s.service.answer("How do I reset my password?").cached


def test_a_question_with_an_email_is_neither_looked_up_nor_stored() -> None:
    s = Setup()
    question = "alice@buzztech.example cannot reset her password"
    s.service.answer(question)
    again = s.service.answer(question)
    assert not again.cached and s.retriever.embeds == 0  # not even embedded for the cache
    assert s.cache.lookup(unit(0.3, 0.3, 0.3, 0.3)) is None


def test_without_a_cache_nothing_changes() -> None:
    chat = FakeChatModel(lambda s, u, j: verdict() if j else "Use the portal.")
    retriever = CountingRetriever()
    service = AnswerService(retriever, chat, AnswerabilityConfig(score_threshold=0.3))  # type: ignore[arg-type]
    service.answer("How do I reset my password?")
    service.answer("How do I reset my password?")
    assert retriever.embeds == 0 and len(retriever.searches) == 2 and len(chat.calls) == 4


class BrokenCache:
    def lookup_exact(self, question: str) -> CachedAnswer | None:
        raise RuntimeError("redis exploded")

    def lookup(self, vector: Any) -> CachedAnswer | None:
        raise RuntimeError("redis exploded")

    def store(self, question: str, vector: Any, answer: CachedAnswer) -> None:
        raise RuntimeError("redis exploded")

    def clear(self) -> None:
        raise RuntimeError("redis exploded")


def test_a_cache_that_raises_never_stops_a_question_being_answered() -> None:
    chat = FakeChatModel(lambda s, u, j: verdict() if j else "Use the portal.")
    service = AnswerService(
        CountingRetriever(),  # type: ignore[arg-type]
        chat,
        AnswerabilityConfig(score_threshold=0.3),
        cache=BrokenCache(),
    )
    result = service.answer("How do I reset my password?")
    assert result.outcome is Outcome.ANSWERED and not result.cached


def test_an_entry_that_cannot_be_read_is_ignored_and_the_question_answered_normally() -> None:
    s = Setup()
    s.cache.store("How do I reset my password?", VECTORS["How do I reset my password?"],
                  CachedAnswer("junk", [{"unexpected": "shape"}]))  # fmt: skip
    result = s.service.answer("How do I reset my password?")
    assert not result.cached and result.reply.startswith("Use the portal.")
