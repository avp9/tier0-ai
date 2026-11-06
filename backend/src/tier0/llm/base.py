from __future__ import annotations

from typing import Protocol


class LLMError(Exception):
    """The language model could not produce a response (network, rate limit, timeout...)."""


class ChatModel(Protocol):
    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str: ...
