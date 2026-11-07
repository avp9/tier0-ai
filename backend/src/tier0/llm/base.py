from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable


class LLMError(Exception):
    """The language model could not produce a response (network, rate limit, timeout...)."""


class ChatModel(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str: ...


@runtime_checkable
class StreamingChatModel(Protocol):
    """A chat model that can hand back its answer in pieces as they are produced."""

    def stream(self, system: str, user: str) -> Iterator[str]: ...
