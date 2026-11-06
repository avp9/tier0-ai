from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from tier0.intent.types import Intent
from tier0.rag.evaluation import RetrievalCase


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent | None = None
    # What the agent is waiting for after this step; "none" means it has finished the turn.
    awaiting: list[str] | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_excludes: list[str] = Field(default_factory=list)


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    say: str = Field(min_length=1)
    expect: Expect = Field(default_factory=Expect)


class SeedTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner: Literal["me", "other"] = "me"
    summary: str
    status: str = "Open"
    public_comment: str | None = None
    internal_comment: str | None = None


class ConversationCase(BaseModel):
    """A scripted conversation and what must be true afterwards.

    kind "routing", "status" and "safety" run ``steps`` in order. A "ticket_flow" opens with the
    first step, answers the agent's detail questions from ``details``, then replies to the
    confirmation with ``confirmations`` (so it copes with a model that asks a little more or less).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["routing", "ticket_flow", "status", "safety"]
    steps: list[Step] = Field(min_length=1)
    seed_tickets: list[SeedTicket] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)
    confirmations: list[str] = Field(default_factory=lambda: ["yes"])
    expect_tickets: int | None = None  # tickets that exist in the ticket system at the end


def load_conversation_cases(path: Path) -> list[ConversationCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [ConversationCase.model_validate(item) for item in raw["cases"]]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("conversation case ids must be unique")
    return cases


class KnowledgeCase(BaseModel):
    """A question the knowledge base should answer, citing one of ``relevant_ids``."""

    id: str
    question: str
    relevant_ids: tuple[str, ...]


class GapCase(BaseModel):
    """A question about something deliberately missing: it must not be answered."""

    id: str
    topic: str
    question: str


def knowledge_cases(retrieval: list[RetrievalCase]) -> list[KnowledgeCase]:
    return [
        KnowledgeCase(id=c.id, question=c.query, relevant_ids=c.relevant_ids) for c in retrieval
    ]


def gap_cases(gaps_path: Path) -> list[GapCase]:
    raw = json.loads(gaps_path.read_text(encoding="utf-8"))
    return [
        GapCase(id=f"gap-{topic['id']}-{index}", topic=topic["id"], question=question)
        for topic in raw["topics"]
        for index, question in enumerate(topic["example_questions"], start=1)
    ]
