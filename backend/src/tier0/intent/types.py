from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class Intent(StrEnum):
    KNOWLEDGE_QUESTION = "KNOWLEDGE_QUESTION"
    CREATE_TICKET = "CREATE_TICKET"
    TICKET_STATUS = "TICKET_STATUS"
    SMALLTALK = "SMALLTALK"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class Domain(StrEnum):
    IT = "IT"
    HR = "HR"
    OTHER = "OTHER"


class Continuity(StrEnum):
    NEW = "NEW"
    CONTINUATION = "CONTINUATION"
    SWITCH = "SWITCH"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class IntentResult(BaseModel):
    """What the employee wants this turn; stored in the conversation state."""

    intent: Intent
    domain: Domain
    continuity: Continuity
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["jev", "openai"]
    model: str = ""

    def is_confident(self, threshold: float = 0.6) -> bool:
        return self.confidence >= threshold


class ClassifierError(Exception):
    """One classifier could not produce a valid result (service error or unusable output)."""


class IntentUnavailable(Exception):
    """Every classifier failed; the agent should apologise and ask the employee to retry."""


class IntentClassifier(Protocol):
    name: str

    def classify(self, message: str, history: list[Turn]) -> IntentResult: ...
