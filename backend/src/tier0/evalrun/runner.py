from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field

from tier0.agent.service import AgentService, Awaiting, ChatTurn
from tier0.evalrun.cases import (
    ConversationCase,
    Expect,
    GapCase,
    KnowledgeCase,
    SeedTicket,
)
from tier0.intent.types import Intent
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.models import TicketDraft

EVAL_USER = "eval.user@buzztech.example"
OTHER_USER = "eval.other@buzztech.example"
MAX_DETAIL_ANSWERS = 5


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile; 0.0 for no data."""
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


@dataclass
class Observed:
    turn: ChatTurn
    seconds: float


@dataclass
class CaseOutcome:
    case_id: str
    kind: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    routing_checks: list[bool] = field(default_factory=list)


@dataclass
class KnowledgeOutcome:
    case_id: str
    answered: bool
    cited_expected: bool
    routed_correctly: bool
    seconds: float
    sources: list[str]
    reply: str = ""
    escalated: bool = False  # the agent offered to raise a ticket


@dataclass
class GapOutcome:
    case_id: str
    topic: str
    escalated: bool
    answered: bool
    routed_correctly: bool
    seconds: float
    reply: str = ""


class Evaluator:
    """Drives the agent the way an employee would and records what happened."""

    def __init__(self, agent: AgentService, adapter: FakeTicketAdapter) -> None:
        self._agent = agent
        self._adapter = adapter
        self._baseline = 0

    def _say(self, thread: str, text: str, email: str = EVAL_USER) -> Observed:
        started = time.perf_counter()
        turn = self._agent.send(thread_id=thread, requester_email=email, message=text)
        return Observed(turn, time.perf_counter() - started)

    # --- knowledge and gaps -----------------------------------------------------------

    def run_knowledge(self, case: KnowledgeCase) -> KnowledgeOutcome:
        seen = self._say(f"k-{uuid.uuid4()}", case.question)
        turn = seen.turn
        sources = [s.document_id for s in turn.sources]
        answered = turn.awaiting is None and bool(sources)
        return KnowledgeOutcome(
            case_id=case.id,
            answered=answered,
            cited_expected=answered and any(s in case.relevant_ids for s in sources),
            routed_correctly=bool(turn.intent and turn.intent.intent is Intent.KNOWLEDGE_QUESTION),
            seconds=seen.seconds,
            sources=sources,
            reply=turn.reply,
            escalated=turn.awaiting is Awaiting.OFFER_TICKET,
        )

    def run_gap(self, case: GapCase) -> GapOutcome:
        seen = self._say(f"g-{uuid.uuid4()}", case.question)
        turn = seen.turn
        return GapOutcome(
            case_id=case.id,
            topic=case.topic,
            escalated=turn.awaiting is Awaiting.OFFER_TICKET,
            answered=turn.awaiting is None and bool(turn.sources),
            routed_correctly=bool(turn.intent and turn.intent.intent is Intent.KNOWLEDGE_QUESTION),
            seconds=seen.seconds,
            reply=turn.reply,
        )

    # --- scripted conversations -------------------------------------------------------

    def run_case(self, case: ConversationCase) -> CaseOutcome:
        self._adapter.reset()
        self._seed(case.seed_tickets)
        self._baseline = len(self._adapter.tickets)
        outcome = CaseOutcome(case.id, case.kind, passed=True)
        thread = f"c-{case.id}-{uuid.uuid4()}"
        if case.kind == "ticket_flow":
            self._ticket_flow(case, thread, outcome)
        else:
            for step in case.steps:
                seen = self._say(thread, step.say)
                self._check(outcome, seen, step.expect)
        if case.expect_tickets is not None:
            actual = len(self._adapter.tickets)
            if actual != case.expect_tickets:
                outcome.failures.append(f"expected {case.expect_tickets} tickets, found {actual}")
        outcome.passed = not outcome.failures
        return outcome

    def _seed(self, tickets: list[SeedTicket]) -> None:
        for seed in tickets:
            email = EVAL_USER if seed.owner == "me" else OTHER_USER
            key = self._adapter.create_ticket(
                TicketDraft(
                    requester_email=email,
                    summary=seed.summary,
                    description="Seeded for evaluation.",
                    category="IT",
                )
            ).key
            self._adapter.set_status(key, seed.status)
            if seed.public_comment:
                self._adapter.add_comment(key, "Support", seed.public_comment)
            if seed.internal_comment:
                self._adapter.add_comment(key, "Support", seed.internal_comment, internal=True)

    def _ticket_flow(self, case: ConversationCase, thread: str, outcome: CaseOutcome) -> None:
        seen = self._say(thread, case.steps[0].say)
        self._check(outcome, seen, case.steps[0].expect)
        details = list(case.details)
        for _ in range(MAX_DETAIL_ANSWERS):
            if seen.turn.awaiting is not Awaiting.TICKET_DETAILS or not details:
                break
            seen = self._say(thread, details.pop(0))
            outcome.latencies.append(seen.seconds)
        if seen.turn.awaiting is Awaiting.OFFER_TICKET:  # a knowledge gap led here
            seen = self._say(thread, "yes")
            outcome.latencies.append(seen.seconds)
            for _ in range(MAX_DETAIL_ANSWERS):
                if seen.turn.awaiting is not Awaiting.TICKET_DETAILS or not details:
                    break
                seen = self._say(thread, details.pop(0))
                outcome.latencies.append(seen.seconds)
        if seen.turn.awaiting is None and case.expect_tickets == 0:
            return  # the employee backed out before the preview: no ticket is the right outcome
        if seen.turn.awaiting is not Awaiting.CONFIRM_TICKET:
            outcome.failures.append(
                f"never reached the confirmation step (waiting for {seen.turn.awaiting})"
            )
            return
        if len(self._adapter.tickets) > self._baseline:
            outcome.failures.append("a ticket existed before the employee confirmed")
        for reply in case.confirmations:
            seen = self._say(thread, reply)
            outcome.latencies.append(seen.seconds)
            if seen.turn.awaiting is not Awaiting.CONFIRM_TICKET:
                break

    @staticmethod
    def _check(outcome: CaseOutcome, seen: Observed, expect: Expect) -> None:
        turn = seen.turn
        outcome.latencies.append(seen.seconds)
        if expect.intent is not None:
            ok = bool(turn.intent and turn.intent.intent is expect.intent)
            outcome.routing_checks.append(ok)
            if not ok:
                got = turn.intent.intent.value if turn.intent else None
                outcome.failures.append(f"intent {got!r}, expected {expect.intent.value!r}")
        if expect.awaiting is not None:
            got_wait = turn.awaiting.value if turn.awaiting else "none"
            if got_wait not in expect.awaiting:
                outcome.failures.append(
                    f"waiting for {got_wait!r}, expected one of {expect.awaiting}"
                )
        text = turn.reply.lower()
        for needle in expect.reply_contains:
            if needle.lower() not in text:
                outcome.failures.append(f"reply lacks {needle!r}")
        for needle in expect.reply_excludes:
            if needle.lower() in text:
                outcome.failures.append(f"reply must not contain {needle!r}")
