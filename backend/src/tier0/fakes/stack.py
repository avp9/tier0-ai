"""Deterministic stand-ins for the language models, used only in local and e2e environments.

They let the whole application run end to end with no API keys: the real web server, database,
retrieval index, agent graph and UI, with predictable "model" behaviour. They are not an
approximation of real answer quality.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from tier0.intent.types import (
    Continuity,
    Domain,
    Intent,
    IntentResult,
    IntentUnavailable,
    Turn,
)
from tier0.llm.base import LLMError
from tier0.rag import answerability, answering, rewrite
from tier0.tickets import drafting
from tier0.tickets.confirmation import Reply, interpret
from tier0.tickets.fake import FakeTicketAdapter

ANSWER_CHARS = 400
_PASSAGE = re.compile(r'<passage id="([^"]+)"[^>]*>(.*?)</passage>', re.DOTALL)
_TICKET_KEY = re.compile(r"\b[A-Za-z][A-Za-z0-9_]+-\d+\b")
_TICKET_REQUEST = re.compile(
    r"(?i)i want to raise a ticket|raise a ticket|create a ticket|open a ticket"
)

_OUT_OF_SCOPE = ("pizza", "weather", "recipe", "joke", "football", "movie", "lottery", "horoscope")
_STATUS = ("status", "my ticket", "my tickets", "any update", "any news", "update on")
_CREATE = ("raise a ticket", "create a ticket", "open a ticket", "log a ticket", "file a ticket")
_SMALLTALK = ("hello", "hi", "hey", "thanks", "thank you", "bye", "goodbye", "good morning")
_HR_WORDS = (
    "leave", "salary", "payroll", "bonus", "holiday", "probation", "onboarding", "benefit", "gym",
    "maternity", "notice period", "stock", "tax", "expense", "reimburse",
)  # fmt: skip
_FOLLOW_UPS = ("and ", "what about", "how do i fix it", "can you explain that", "why ")


class RuleBasedClassifier:
    """Decides the intent from keywords, so e2e runs are predictable."""

    name = "fake"

    def __init__(self) -> None:
        self._failures = 0

    def fail_next(self, times: int = 1) -> None:
        self._failures = times

    def classify(self, message: str, history: list[Turn]) -> IntentResult:
        if self._failures > 0:
            self._failures -= 1
            raise IntentUnavailable("scripted classifier outage")
        text = message.strip().lower()
        words = text.rstrip("?!. ")
        intent = Intent.KNOWLEDGE_QUESTION
        if any(k in text for k in _OUT_OF_SCOPE):
            intent = Intent.OUT_OF_SCOPE
        elif any(k in text for k in _CREATE):
            intent = Intent.CREATE_TICKET
        elif any(k in text for k in _STATUS) or _TICKET_KEY.search(message):
            intent = Intent.TICKET_STATUS
        elif words in _SMALLTALK:
            intent = Intent.SMALLTALK
        elif len(words) < 3 or words == "help":
            intent = Intent.AMBIGUOUS
        domain = Domain.HR if any(k in text for k in _HR_WORDS) else Domain.IT
        if intent in (Intent.SMALLTALK, Intent.OUT_OF_SCOPE):
            domain = Domain.OTHER
        continuing = bool(history) and text.startswith(_FOLLOW_UPS)
        return IntentResult(
            intent=intent,
            domain=domain,
            continuity=Continuity.CONTINUATION if continuing else Continuity.NEW,
            confidence=0.4 if intent is Intent.AMBIGUOUS else 0.9,
            source="fake",
            model="rule-based",
        )


def _describes_a_problem(text: str) -> bool:
    core = _TICKET_REQUEST.sub("", text).strip(" :,.")
    return len(core) > 12 and interpret(core) is Reply.UNCLEAR


class ScriptedChatModel:
    """Answers each prompt the agent sends, from the passages and conversation it is given."""

    def __init__(self) -> None:
        self._failures = 0

    def fail_next(self, times: int = 1) -> None:
        self._failures = times

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> str:
        if self._failures > 0:
            self._failures -= 1
            raise LLMError("scripted language model outage")
        if system == answerability.SUFFICIENCY_SYSTEM:
            return self._sufficiency(user)
        if system == answering.ANSWER_SYSTEM:
            return self._answer(user)
        if system == rewrite.SYSTEM_PROMPT:
            return ""  # the rewriter then falls back to joining the last question and this one
        if system == drafting.SYSTEM_PROMPT:
            return self._draft(user)
        raise LLMError("scripted model received an unexpected prompt")

    @staticmethod
    def _sufficiency(user: str) -> str:
        first = _PASSAGE.search(user)
        ids = [first.group(1)] if first else []
        return json.dumps({"sufficient": bool(ids), "reason": "scripted", "supporting_ids": ids})

    @staticmethod
    def _answer(user: str) -> str:
        first = _PASSAGE.search(user)
        text = html.unescape(first.group(2)) if first else ""
        text = text.split("Answer:", 1)[-1]
        text = " ".join(text.split())
        if len(text) > ANSWER_CHARS:
            text = text[: ANSWER_CHARS - 1] + "…"
        return f"Here is what our knowledge base says: {text}"

    @staticmethod
    def _draft(user: str) -> str:
        said = [t["content"] for t in json.loads(user)["conversation"] if t["role"] == "user"]
        category = {"it": "IT", "hr": "HR"}.get(said[-1].strip().lower()) if said else None
        problems = [s for s in said if _describes_a_problem(s)]
        if not problems:
            return json.dumps({"summary": None, "reported": [], "category": category})
        return json.dumps(
            {
                "summary": problems[-1][:70],
                "reported": problems,
                "already_tried": [],
                "assumptions": [],
                "category": category,
                "priority": None,
            }
        )


@dataclass(frozen=True)
class FakeControls:
    """Handles on the fake parts, for the e2e control endpoints."""

    adapter: FakeTicketAdapter | None
    chat: ScriptedChatModel | None
    classifier: RuleBasedClassifier | None
