"""The Redis-backed cache against a real Redis (8, or Redis Stack: anything with the search module).

Run with TIER0_TEST_REDIS_URL=redis://localhost:6379 (CI sets it and requires it).
"""

import contextlib
import math
import os
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tier0.rag.semantic_cache import CachedAnswer, RedisSemanticCache


def _url() -> str:
    url = os.environ.get("TIER0_TEST_REDIS_URL")
    if url:
        return url
    if os.environ.get("REQUIRE_REDIS_TESTS") == "1":
        pytest.fail("REQUIRE_REDIS_TESTS=1 but TIER0_TEST_REDIS_URL is not set")
    pytest.skip("set TIER0_TEST_REDIS_URL to run Redis tests")


@pytest.fixture
def redis_url() -> str:
    return _url()


Make = Callable[..., RedisSemanticCache]


@pytest.fixture
def make(redis_url: str) -> Iterator[Make]:
    """Caches with a namespace of their own, removed afterwards."""
    created: list[RedisSemanticCache] = []

    def factory(namespace: str | None = None, **kwargs: Any) -> RedisSemanticCache:
        cache = RedisSemanticCache(redis_url, namespace or f"test{uuid.uuid4().hex[:10]}", **kwargs)
        created.append(cache)
        return cache

    yield factory
    for cache in created:
        with contextlib.suppress(Exception):  # the index or the keys may never have been created
            if cache._cache is not None:
                cache._cache.delete()
            if cache._client is not None:
                for key in cache._client.scan_iter(f"tier0:semcache:exact:{cache._namespace}:*"):
                    cache._client.delete(key)


def hit(cache: RedisSemanticCache, vector: list[float]) -> CachedAnswer | None:
    """A lookup that allows for Redis finishing indexing an entry a moment after it was stored."""
    found = None
    for _ in range(60):
        found = cache.lookup(vector)
        if found is not None:
            break
        time.sleep(0.05)
    return found


def unit(*values: float) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values]


ANSWER = CachedAnswer(
    "Use the portal. Réinitialiser ✓\n\nSources: Reset a password",
    [{"document_id": "d1", "title": "Reset a password", "category": "IT", "score": 0.81}],
)


def test_an_identical_question_gets_the_stored_answer_back_intact(make: Make) -> None:
    cache = make(min_similarity=0.95)
    assert cache.lookup(unit(1, 0, 0, 0)) is None  # nothing yet (and the index is created)
    cache.store("how do I reset my password", unit(1, 0, 0, 0), ANSWER)
    assert hit(cache, unit(1, 0, 0, 0)) == ANSWER


def test_the_exact_question_is_found_without_a_vector_whatever_its_capitals_and_punctuation(
    make: Make,
) -> None:
    cache = make()
    assert cache.lookup_exact("How do I reset my password?") is None
    cache.store("How do I reset my password?", unit(1, 0, 0, 0), ANSWER)
    for variant in (
        "How do I reset my password?",
        "how do i reset my password",
        "  HOW DO I RESET MY PASSWORD?!  ",
    ):
        assert cache.lookup_exact(variant) == ANSWER
    assert cache.lookup_exact("how can I reset my password") is None


def test_the_exact_match_does_not_need_the_embedding_index_at_all(redis_url: str) -> None:
    cache = RedisSemanticCache(redis_url, f"exactonly{uuid.uuid4().hex[:8]}")
    assert cache.lookup_exact("anything at all") is None
    assert cache._cache is None  # the vector index was never created for it


def test_the_exact_match_expires_and_stays_inside_its_namespace(make: Make) -> None:
    short, other = make(ttl_seconds=1), make()
    short.store("q one", unit(1, 0, 0, 0), ANSWER)
    assert short.lookup_exact("q one") == ANSWER and other.lookup_exact("q one") is None
    time.sleep(2.2)
    assert short.lookup_exact("q one") is None


def test_clearing_removes_this_namespaces_answers_and_leaves_the_others(make: Make) -> None:
    mine, theirs = make(), make()
    for cache in (mine, theirs):
        cache.store("q one", unit(1, 0, 0, 0), ANSWER)
    mine.clear()
    assert mine.lookup_exact("q one") is None and mine.lookup(unit(1, 0, 0, 0)) is None
    assert theirs.lookup_exact("q one") == ANSWER and hit(theirs, unit(1, 0, 0, 0)) == ANSWER


def test_a_stored_answer_is_found_both_exactly_and_by_meaning(make: Make) -> None:
    cache = make(min_similarity=0.95)
    cache.store("How do I reset my password?", unit(1, 0, 0, 0), ANSWER)
    assert cache.lookup_exact("how do i reset my password") == ANSWER
    assert hit(cache, unit(1, 0.1, 0, 0)) == ANSWER


def test_a_similar_question_hits_and_a_different_one_misses(make: Make) -> None:
    cache = make(min_similarity=0.95)
    cache.store("q", unit(1, 0, 0, 0), ANSWER)
    assert hit(cache, unit(1, 0.15, 0, 0)) == ANSWER  # similarity about 0.99
    assert cache.lookup(unit(1, 0.6, 0, 0)) is None  # about 0.86
    assert cache.lookup(unit(0, 1, 0, 0)) is None


def test_the_similarity_setting_decides_how_close_is_close_enough(make: Make) -> None:
    strict, loose = make(min_similarity=0.99), make(min_similarity=0.80)
    for cache in (strict, loose):
        cache.store("q", unit(1, 0, 0, 0), ANSWER)
    nearby = unit(1, 0.4, 0, 0)  # about 0.93
    assert strict.lookup(nearby) is None and loose.lookup(nearby) == ANSWER


def test_entries_expire(make: Make) -> None:
    cache = make(ttl_seconds=1)
    cache.store("q", unit(1, 0, 0, 0), ANSWER)
    assert hit(cache, unit(1, 0, 0, 0)) == ANSWER
    time.sleep(2.2)
    assert cache.lookup(unit(1, 0, 0, 0)) is None


def test_a_different_namespace_never_sees_the_entry(make: Make) -> None:
    old, new = make(namespace="index-v1"), make(namespace="index-v2")
    old.store("q", unit(1, 0, 0, 0), ANSWER)
    assert new.lookup(unit(1, 0, 0, 0)) is None
    assert hit(old, unit(1, 0, 0, 0)) == ANSWER


def test_vectors_of_a_different_size_get_their_own_index(make: Make) -> None:
    cache = make(namespace=f"dims{uuid.uuid4().hex[:8]}")
    cache.store("q", unit(1, 0, 0, 0), ANSWER)
    assert cache.lookup(unit(1, 0, 0, 0, 0, 0)) is None  # no error, just a miss
    cache.store("q6", unit(1, 0, 0, 0, 0, 0), CachedAnswer("six", []))
    assert hit(cache, unit(1, 0, 0, 0, 0, 0)) == CachedAnswer("six", [])
    assert hit(cache, unit(1, 0, 0, 0)) == ANSWER  # the 4-wide index is a separate one


def test_a_second_instance_shares_the_entries_like_another_server_would(make: Make) -> None:
    first = make(namespace="shared-ns")
    first.store("q", unit(1, 0, 0, 0), ANSWER)
    second = make(namespace="shared-ns")
    assert hit(second, unit(1, 0, 0, 0)) == ANSWER


def test_an_unreadable_entry_is_a_miss_and_does_not_count_as_an_outage(make: Make) -> None:
    cache = make()
    cache.store("q", unit(1, 0, 0, 0), ANSWER)
    cache._cache.store(prompt="bad", response="not json", vector=unit(0, 1, 0, 0))
    assert cache.lookup(unit(0, 1, 0, 0)) is None
    assert hit(cache, unit(1, 0, 0, 0)) == ANSWER  # still working


def test_when_redis_is_unreachable_it_fails_open_and_stops_trying_for_a_while() -> None:
    now = [100.0]
    cache = RedisSemanticCache(
        "redis://127.0.0.1:1",  # nothing listens here
        "down",
        timeout_seconds=0.2,
        cooldown_seconds=30,
        clock=lambda: now[0],
    )
    started = time.perf_counter()
    assert cache.lookup_exact("a question") is None  # no exception, whichever step is tried
    cache._down_until = 0.0
    assert cache.lookup(unit(1, 0, 0, 0)) is None  # no exception
    first = time.perf_counter() - started
    cache.store("q", unit(1, 0, 0, 0), ANSWER)  # skipped quickly, no exception
    assert cache.lookup(unit(1, 0, 0, 0)) is None
    assert time.perf_counter() - started < first + 0.5  # the later calls did not wait on Redis
    now[0] += 31  # the cool-down is over: it tries again (and fails again, quietly)
    assert cache.lookup(unit(1, 0, 0, 0)) is None


def test_it_recovers_when_redis_comes_back(redis_url: str) -> None:
    now = [100.0]
    cache = RedisSemanticCache(
        redis_url, f"recover{uuid.uuid4().hex[:8]}", cooldown_seconds=10, clock=lambda: now[0]
    )
    cache._down_until = 105.0  # pretend an outage just ended
    assert cache.lookup(unit(1, 0, 0, 0)) is None and cache._cache is None  # still cooling down
    now[0] = 106.0
    cache.store("q", unit(1, 0, 0, 0), ANSWER)
    assert hit(cache, unit(1, 0, 0, 0)) == ANSWER
    assert cache._cache is not None
    cache._cache.delete()
    for key in cache._exact_client().scan_iter(f"tier0:semcache:exact:{cache._namespace}:*"):
        cache._exact_client().delete(key)
