from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

Handler = Callable[[str, str, bool], str]


@dataclass
class Call:
    system: str
    user: str
    json_mode: bool


@dataclass
class FakeChatModel:
    """Scriptable stand-in for a chat model; records every call it receives."""

    handler: Handler
    calls: list[Call] = field(default_factory=list)

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        self.calls.append(Call(system=system, user=user, json_mode=json_mode))
        return self.handler(system, user, json_mode)

    def stream(self, system: str, user: str) -> Iterator[str]:
        """The scripted reply, a word at a time."""
        yield from words(self.complete(system, user))


def words(text: str) -> Iterator[str]:
    """Split text into pieces that join back to exactly the same text."""
    yield from re.findall(r"\S+\s*|\s+", text)
