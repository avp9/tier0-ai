import json
import re
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from tier0.agent.graph import AgentDependencies
from tier0.agent.service import (
    AgentService,
    ChatTurn,
    build_agent,
)
from tier0.intent.types import (
    Continuity,
    Domain,
    Intent,
    IntentResult,
    IntentUnavailable,
    Turn,
)
from tier0.llm.fake import FakeChatModel
from tier0.observability.tracing import Tracing
from tier0.rag import answerability, answering, rewrite
from tier0.rag.answerability import AnswerabilityConfig
from tier0.rag.answering import AnswerService
from tier0.rag.retriever import Retriever
from tier0.rag.semantic_cache import SemanticCache
from tier0.tickets import drafting
from tier0.tickets.confirmation import Reply, interpret
from tier0.tickets.drafting import TicketDrafter
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.models import TicketDraft
from tier0.tickets.status import TicketStatusService
from tier0.tickets.submission import InMemorySubmissionStore, TicketSubmissionService

ALICE, BOB = "alice@buzztech.example", "bob@buzztech.example"
ANSWERABLE = "bring photo ID, proof of address and signed offer letter"
GAP = "Does BuzzTech reimburse gym memberships?"
SCORE_THRESHOLD = 0.35  # between the fake embedder's 0.444 (answerable) and 0.264 (gap)


def _describes_a_problem(text: str) -> bool:
    """What a real model would do: "raise a ticket" alone says nothing about the problem."""
    core = re.sub(r"(?i)i want to raise a ticket|raise a ticket|maybe a ticket", "", text)
    core = core.strip(" :,.")
    return len(core) > 12 and interpret(core) is Reply.UNCLEAR


class ScriptedClassifier:
    """Stands in for JEV/OpenAI: decides the intent from the wording of the test messages."""

    name = "scripted"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = False
        self.overrides: dict[str, IntentResult] = {}

    def classify(self, message: str, history: list[Turn]) -> IntentResult:
        self.calls.append((message, len(history)))
        if self.fail:
            raise IntentUnavailable("both down")
        if message in self.overrides:
            return self.overrides[message]
        text = message.lower()
        intent, domain, conf = Intent.KNOWLEDGE_QUESTION, Domain.IT, 0.9
        if "gym" in text:
            domain = Domain.HR
        if "status" in text or "my ticket" in text or "tickets" in text or "ticket sup" in text:
            intent = Intent.TICKET_STATUS
        elif "raise a ticket" in text or "create the ticket" in text:
            intent = Intent.CREATE_TICKET
        elif "pizza" in text:
            intent, domain = Intent.OUT_OF_SCOPE, Domain.OTHER
        elif text in ("hello", "hi", "thanks", "thank you", "bye"):
            intent, domain = Intent.SMALLTALK, Domain.OTHER
        elif text == "help":
            intent, conf = Intent.AMBIGUOUS, 0.4
        continuity = Continuity.CONTINUATION if text.startswith("how do i fix") else Continuity.NEW
        return IntentResult(intent=intent, domain=domain, continuity=continuity,
                            confidence=conf, source="jev")  # fmt: skip


class SpyAnswers(AnswerService):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.questions: list[str] = []

    def answer(self, question, on_token=None):  # type: ignore[no-untyped-def]
        self.questions.append(question)
        return super().answer(question, on_token)


class Harness:
    def __init__(
        self,
        retriever: Retriever,
        cache: SemanticCache | None = None,
        tracing: Tracing | None = None,
    ) -> None:
        self.rewrite_output = "I cannot connect to the company VPN from home"
        self.llm = FakeChatModel(self._respond)
        self.classifier = ScriptedClassifier()
        self.adapter = FakeTicketAdapter()
        self.answers = SpyAnswers(
            retriever, self.llm, AnswerabilityConfig(score_threshold=SCORE_THRESHOLD), cache=cache
        )
        submission = TicketSubmissionService(self.adapter, InMemorySubmissionStore())
        deps = AgentDependencies(
            classifier=self.classifier,
            answers=self.answers,
            rewriter=rewrite.QuestionRewriter(self.llm),
            drafter=TicketDrafter(self.llm),
            submission=submission,
            status=TicketStatusService(self.adapter),
        )
        self.agent: AgentService = build_agent(deps, MemorySaver(), tracing)

    def _respond(self, system: str, user: str, _json_mode: bool) -> str:
        if system == answerability.SUFFICIENCY_SYSTEM:
            first = re.search(r'<passage id="([^"]+)"', user)
            ids = [first.group(1)] if first else []
            return json.dumps({"sufficient": True, "reason": "ok", "supporting_ids": ids})
        if system == answering.ANSWER_SYSTEM:
            return "Here is what the handbook says."
        if system == rewrite.SYSTEM_PROMPT:
            return self.rewrite_output
        if system == drafting.SYSTEM_PROMPT:
            said = [t["content"] for t in json.loads(user)["conversation"] if t["role"] == "user"]
            problems = [s for s in said if _describes_a_problem(s)]
            if not problems:
                return json.dumps({"summary": None, "reported": []})
            return json.dumps({"summary": problems[-1][:70], "reported": problems})
        raise AssertionError(f"unexpected prompt: {system[:40]}")

    def say(self, text: str, thread: str = "t1", email: str = ALICE) -> ChatTurn:
        return self.agent.send(thread_id=thread, requester_email=email, message=text)

    def add_ticket(self, email: str, summary: str = "Laptop slow") -> str:
        draft = TicketDraft(requester_email=email, summary=summary,
                            description="details", category="IT")  # fmt: skip
        return self.adapter.create_ticket(draft).key
