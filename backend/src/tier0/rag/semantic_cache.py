"""A semantic cache for answers: a question that means the same as one answered before gets the
stored answer, without searching the knowledge base or calling a language model again.

A question is matched in two steps. First the exact question, ignoring capitals, punctuation and
spacing: a plain key lookup that needs no embedding and cannot be wrong. Then by meaning: the
similarity of its embedding to answered questions (RedisVL's semantic cache), which also catches
rewordings. Entries expire after a while. The cache is an optimisation only: if it is unreachable
or misbehaves, the question is simply answered the normal way.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

MAX_QUESTION_CHARS = 300
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LONG_NUMBER = re.compile(r"\d(?:[ -]?\d){8,}")  # a phone, card or account number


def cacheable(question: str) -> bool:
    """Whether a question may be stored. Questions carrying personal details are never kept:
    the cache is shared by everyone, so a stored question is visible to the whole workforce's
    lookups and lives in Redis until it expires."""
    text = question.strip()
    if not text or len(text) > MAX_QUESTION_CHARS:
        return False
    return not (_EMAIL.search(text) or _LONG_NUMBER.search(text))


def normalize_question(question: str) -> str:
    """The question without capitals, punctuation or extra spacing, so that "How do I reset my
    password?" and "how do i reset my password?!" are recognised as the same question."""
    text = unicodedata.normalize("NFKC", question).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def cache_namespace(*parts: object) -> str:
    """A short fingerprint of everything an answer depends on. Anything that changes the answer
    (the knowledge index, the models, the prompts, the settings) gives a different namespace, so
    old entries are never served for the new setup; they simply expire."""
    return hashlib.sha256(json.dumps([str(p) for p in parts]).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class CachedAnswer:
    """What is stored for a question: the finished reply and the sources it cited."""

    reply: str
    sources: list[dict[str, Any]] = field(default_factory=list)


class SemanticCache(Protocol):
    def lookup_exact(self, question: str) -> CachedAnswer | None:
        """The stored answer to this very question (ignoring capitals and punctuation)."""
        ...

    def lookup(self, vector: Sequence[float]) -> CachedAnswer | None:
        """The stored answer to the most similar question, if it is similar enough."""
        ...

    def store(self, question: str, vector: Sequence[float], answer: CachedAnswer) -> None: ...

    def clear(self) -> None:
        """Forget every stored answer. Only the test controls use this."""
        ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class InMemorySemanticCache:
    """The same behaviour without Redis, for tests and for running with no Redis at all."""

    def __init__(
        self,
        *,
        min_similarity: float = 0.88,
        ttl_seconds: int = 86400,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min = min_similarity
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: list[tuple[str, list[float], CachedAnswer, float]] = []
        self._lock = threading.Lock()

    def lookup_exact(self, question: str) -> CachedAnswer | None:
        key, now = normalize_question(question), self._clock()
        with self._lock:
            self._entries = [e for e in self._entries if e[3] > now]
            for stored, _vector, answer, _expires in self._entries:
                if normalize_question(stored) == key:
                    return answer
        return None

    def lookup(self, vector: Sequence[float]) -> CachedAnswer | None:
        now = self._clock()
        with self._lock:
            self._entries = [e for e in self._entries if e[3] > now]
            best: tuple[float, CachedAnswer] | None = None
            for _question, stored, answer, _expires in self._entries:
                similarity = _cosine(vector, stored)
                if similarity >= self._min and (best is None or similarity > best[0]):
                    best = (similarity, answer)
        return best[1] if best else None

    def store(self, question: str, vector: Sequence[float], answer: CachedAnswer) -> None:
        with self._lock:
            self._entries.append((question, list(vector), answer, self._clock() + self._ttl))

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class RedisSemanticCache:
    """RedisVL's semantic cache behind the ``SemanticCache`` interface.

    The index is created on first use, sized from the first vector, so nothing is contacted when
    the application starts. It fails open: an error means "no cached answer" and the cache is
    left alone for a short while, so a Redis outage costs nothing per question.
    """

    def __init__(
        self,
        url: str,
        namespace: str,
        *,
        min_similarity: float = 0.88,
        ttl_seconds: int = 86400,
        timeout_seconds: float = 0.5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._namespace = namespace
        self._min = min_similarity
        self._ttl = ttl_seconds
        self._timeout = timeout_seconds
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._cache: Any = None
        self._client: Any = None
        self._dimensions = 0
        self._down_until = 0.0
        self._lock = threading.Lock()

    def _exact_client(self) -> Any:
        with self._lock:
            if self._client is None:
                import redis

                self._client = redis.Redis.from_url(
                    self._url,
                    socket_timeout=self._timeout,
                    socket_connect_timeout=self._timeout,
                )
            return self._client

    def _exact_key(self, question: str) -> str:
        digest = hashlib.sha256(normalize_question(question).encode()).hexdigest()[:32]
        return f"tier0:semcache:exact:{self._namespace}:{digest}"

    @staticmethod
    def _read(raw: bytes | str) -> CachedAnswer:
        body = json.loads(raw)
        return CachedAnswer(reply=str(body["reply"]), sources=list(body.get("sources", [])))

    def clear(self) -> None:
        """Delete this namespace's answers (the exact ones and the ones found by meaning)."""
        try:
            client = self._exact_client()
            for pattern in (
                f"tier0:semcache:exact:{self._namespace}:*",
                f"tier0:semcache:{self._namespace}:*",
            ):
                for key in client.scan_iter(pattern):
                    client.delete(key)
        except Exception as error:
            self._failed("clear", error)

    def _open(self, dimensions: int) -> Any:
        """Create the cache for vectors of this size (once, however many requests ask)."""
        with self._lock:
            if self._cache is None or self._dimensions != dimensions:
                from redisvl.extensions.cache.llm import SemanticCache as RedisVLCache
                from redisvl.utils.vectorize import CustomVectorizer

                self._cache = RedisVLCache(
                    name=f"tier0:semcache:{self._namespace}:{dimensions}",
                    distance_threshold=1.0 - self._min,
                    ttl=self._ttl,
                    # Vectors are always supplied, so this is never called; it only gives the
                    # index its size without a network call.
                    vectorizer=CustomVectorizer(embed=lambda *_a, **_k: [0.0] * dimensions),
                    redis_url=self._url,
                    connection_kwargs={
                        "socket_timeout": self._timeout,
                        "socket_connect_timeout": self._timeout,
                    },
                )
                self._dimensions = dimensions
            return self._cache

    def _available(self) -> bool:
        return self._clock() >= self._down_until

    def _failed(self, action: str, error: Exception) -> None:
        self._down_until = self._clock() + self._cooldown
        log.warning(
            "semantic cache %s failed; skipping it for %.0f s: %s",
            action,
            self._cooldown,
            type(error).__name__,
        )

    def lookup_exact(self, question: str) -> CachedAnswer | None:
        if not self._available() or not normalize_question(question):
            return None
        try:
            raw = self._exact_client().get(self._exact_key(question))
            return self._read(raw) if raw is not None else None
        except (KeyError, TypeError, ValueError):
            return None
        except Exception as error:
            self._failed("exact lookup", error)
            return None

    def lookup(self, vector: Sequence[float]) -> CachedAnswer | None:
        if not self._available():
            return None
        try:
            found = self._open(len(vector)).check(vector=list(vector), num_results=1)
            return self._read(found[0]["response"]) if found else None
        except (KeyError, TypeError, ValueError):
            return None  # an entry that cannot be read is a miss, not an outage
        except Exception as error:
            self._failed("lookup", error)
            return None

    def store(self, question: str, vector: Sequence[float], answer: CachedAnswer) -> None:
        if not self._available():
            return
        body = json.dumps({"reply": answer.reply, "sources": answer.sources})
        try:
            self._exact_client().set(self._exact_key(question), body, ex=self._ttl)
            self._open(len(vector)).store(prompt=question, response=body, vector=list(vector))
        except Exception as error:
            self._failed("store", error)
