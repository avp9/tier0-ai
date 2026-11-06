from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from tier0.fakes.stack import ScriptedChatModel
from tier0.llm.openai_chat import OpenAIChatModel
from tier0.rag.embeddings import FakeEmbedder, OpenAIEmbedder

CHARS_PER_TOKEN = 4  # a rough rule of thumb; real token counts differ


@dataclass
class Prices:
    """US dollars per million tokens. Defaults are placeholders: check the provider's price list."""

    chat_input: float = 0.15
    chat_output: float = 0.60
    embedding: float = 0.02


@dataclass
class Usage:
    chat_calls: int = 0
    chat_input_chars: int = 0
    chat_output_chars: int = 0
    embed_calls: int = 0
    embed_chars: int = 0
    _by_call: list[int] = field(default_factory=list)

    def estimate_dollars(self, prices: Prices) -> float:
        per_million = 1_000_000 * CHARS_PER_TOKEN
        return (
            self.chat_input_chars * prices.chat_input / per_million
            + self.chat_output_chars * prices.chat_output / per_million
            + self.embed_chars * prices.embedding / per_million
        )


@contextmanager
def instrument(usage: Usage) -> Iterator[None]:
    """Count language-model and embedding traffic by wrapping the provider classes' methods."""
    originals: list[tuple[Any, str, Callable[..., Any]]] = []

    def wrap_chat(cls: Any) -> None:
        original = cls.complete

        def complete(self: Any, system: str, user: str, **kwargs: Any) -> str:
            usage.chat_calls += 1
            usage.chat_input_chars += len(system) + len(user)
            result: str = original(self, system, user, **kwargs)
            usage.chat_output_chars += len(result)
            return result

        originals.append((cls, "complete", original))
        cls.complete = complete

    def wrap_embed(cls: Any) -> None:
        original = cls.embed

        def embed(self: Any, texts: list[str]) -> list[list[float]]:
            usage.embed_calls += 1
            usage.embed_chars += sum(len(t) for t in texts)
            result: list[list[float]] = original(self, texts)
            return result

        originals.append((cls, "embed", original))
        cls.embed = embed

    for chat_class in (OpenAIChatModel, ScriptedChatModel):
        wrap_chat(chat_class)
    for embed_class in (OpenAIEmbedder, FakeEmbedder):
        wrap_embed(embed_class)
    try:
        yield
    finally:
        for cls, name, original in originals:
            setattr(cls, name, original)
