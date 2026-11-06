"""Endpoints that let end-to-end tests set up and inspect the fake stack.

Registered only when ``Settings.e2e_controls_enabled`` (APP_ENV=e2e, fake providers, a token).
A missing or wrong token gets a 404, indistinguishable from the route not existing.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from tier0.auth.ratelimit import LoginRateLimiter, SlidingWindowLimiter
from tier0.fakes.stack import FakeControls
from tier0.tickets.errors import TicketOutcomeUnknown, TicketServiceUnavailable
from tier0.tickets.fake import FakeTicketAdapter
from tier0.tickets.models import TicketDraft

router = APIRouter(prefix="/api/_e2e", include_in_schema=False)


def controls(request: Request, x_e2e_token: Annotated[str | None, Header()] = None) -> FakeControls:
    expected = request.app.state.settings.e2e_token
    if (
        expected is None
        or x_e2e_token is None
        or not hmac.compare_digest(x_e2e_token, expected.get_secret_value())
    ):
        raise HTTPException(404, "Not Found")
    found: FakeControls | None = getattr(request.app.state, "e2e_controls", None)
    if found is None or found.adapter is None:
        raise HTTPException(503, "The fake stack is not running")
    return found


def _adapter(found: FakeControls) -> FakeTicketAdapter:
    if found.adapter is None:
        raise HTTPException(503, "The fake stack is not running")
    return found.adapter


Controls = Annotated[FakeControls, Depends(controls)]


class SeedComment(BaseModel):
    author: str = "Support"
    body: str
    internal: bool = False


class SeedTicket(BaseModel):
    email: str
    summary: str = Field(min_length=1, max_length=255)
    description: str = "Seeded by an end-to-end test."
    category: Literal["IT", "HR"] = "IT"
    status: str = "Open"
    comments: list[SeedComment] = Field(default_factory=list)


class Failure(BaseModel):
    target: Literal[
        "tickets_unavailable",
        "tickets_unknown_outcome",
        "tickets_lost_response",
        "llm",
        "classifier",
    ]
    times: int = Field(default=1, ge=1, le=20)


@router.post("/reset")
def reset(request: Request, found: Controls) -> dict[str, str]:
    """Forget tickets, scripted failures and rate-limit counters."""
    adapter = _adapter(found)
    adapter.reset()
    if found.chat:
        found.chat.fail_next(0)
    if found.classifier:
        found.classifier.fail_next(0)
    settings = request.app.state.settings
    request.app.state.chat_limiter = SlidingWindowLimiter(settings.chat_messages_per_minute, 60)
    request.app.state.limiter = LoginRateLimiter(
        settings.login_max_failures_per_email,
        settings.login_max_failures_per_ip,
        settings.login_window_minutes * 60,
    )
    return {"status": "reset"}


@router.get("/tickets")
def list_tickets(found: Controls) -> list[dict[str, object]]:
    adapter = _adapter(found)
    return [
        {
            "key": t.key,
            "requester_email": t.draft.requester_email,
            "summary": t.draft.summary,
            "description": t.draft.description,
            "category": t.draft.category,
            "priority": t.draft.priority,
            "status": t.status,
            "submission_id": t.draft.submission_id,
        }
        for t in adapter.tickets
    ]


@router.post("/tickets", status_code=201)
def seed_ticket(body: SeedTicket, found: Controls) -> dict[str, str]:
    adapter = _adapter(found)
    draft = TicketDraft(
        requester_email=body.email,
        summary=body.summary,
        description=body.description,
        category=body.category,
    )
    key = adapter.create_ticket(draft).key
    adapter.set_status(key, body.status)
    for comment in body.comments:
        adapter.add_comment(key, comment.author, comment.body, internal=comment.internal)
    return {"key": key}


@router.post("/fail")
def fail(body: Failure, found: Controls) -> dict[str, str]:
    adapter = _adapter(found)
    if body.target == "tickets_unavailable":
        adapter.fail_next(TicketServiceUnavailable("scripted outage"))
    elif body.target == "tickets_unknown_outcome":
        adapter.fail_next(TicketOutcomeUnknown("scripted timeout"))
    elif body.target == "tickets_lost_response":
        adapter.lose_next_create_response()
    elif body.target == "llm" and found.chat:
        found.chat.fail_next(body.times)
    elif body.target == "classifier" and found.classifier:
        found.classifier.fail_next(body.times)
    else:
        raise HTTPException(409, "That part is not faked in this environment")
    return {"status": "armed"}
