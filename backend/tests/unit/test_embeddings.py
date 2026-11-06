import math
from types import SimpleNamespace
from typing import Any

from tier0.rag.embeddings import OPENAI_BATCH_SIZE, FakeEmbedder, OpenAIEmbedder


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_fake_embedder_is_deterministic_and_normalised() -> None:
    embedder = FakeEmbedder()
    first, second = embedder.embed(["Reset my password"]), embedder.embed(["Reset my password"])
    assert first == second
    assert math.isclose(math.sqrt(sum(v * v for v in first[0])), 1.0)


def test_fake_embedder_places_similar_wording_closer() -> None:
    embedder = FakeEmbedder()
    query, close, far = embedder.embed(
        [
            "how do I reset my password",
            "reset a forgotten password",
            "stock option vesting schedule",
        ]
    )
    assert cosine(query, close) > cosine(query, far)


def test_fake_embedder_handles_empty_text() -> None:
    (vector,) = FakeEmbedder().embed([""])
    assert len(vector) == 256 and not any(vector)


class StubEmbeddings:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def create(self, model: str, input: list[str]) -> Any:  # noqa: A002
        self.calls.append(len(input))
        data = [
            SimpleNamespace(index=i, embedding=[float(len(text))]) for i, text in enumerate(input)
        ]
        return SimpleNamespace(data=list(reversed(data)))


def test_openai_embedder_batches_and_restores_order() -> None:
    embedder = OpenAIEmbedder(api_key="test-key")
    stub = StubEmbeddings()
    embedder._client = SimpleNamespace(embeddings=stub)  # type: ignore[assignment]
    texts = ["x" * n for n in range(1, 2 * OPENAI_BATCH_SIZE + 45)]
    vectors = embedder.embed(texts)
    assert stub.calls == [OPENAI_BATCH_SIZE, OPENAI_BATCH_SIZE, 44]
    assert [v[0] for v in vectors] == [float(len(t)) for t in texts]


def test_openai_model_id_names_the_model() -> None:
    assert OpenAIEmbedder(api_key="k", model="m").model_id == "openai:m"
