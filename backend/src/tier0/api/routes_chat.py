from __future__ import annotations

import logging
import time
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status

from tier0.agent.service import AgentService, ThreadNotFound
from tier0.api.deps import current_user, same_origin
from tier0.api.schemas import (
    ChatRequest,
    ChatResponse,
    HistoryResponse,
    to_chat_response,
    to_history_response,
)
from tier0.auth.users import User
from tier0.observability.context import bind_thread

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

NOT_FOUND = "Conversation not found"


def _agent(request: Request) -> AgentService:
    agent: AgentService | None = request.app.state.agent
    if agent is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chat is not available right now")
    return agent


@router.post("", response_model=ChatResponse, dependencies=[Depends(same_origin)])
def send_message(
    body: ChatRequest, request: Request, user: Annotated[User, Depends(current_user)]
) -> ChatResponse:
    agent = _agent(request)
    if body.thread_id:
        bind_thread(str(body.thread_id), str(user.id))
    wait = request.app.state.chat_limiter.hit(user.id)
    if wait is not None:
        log.warning("chat rate limit reached", extra={"fields": {"retry_after_s": wait}})
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You're sending messages too quickly. Please wait a moment.",
            headers={"Retry-After": str(wait)},
        )
    thread_id = str(body.thread_id or uuid4())
    bind_thread(thread_id, str(user.id))
    started = time.perf_counter()
    try:
        # The requester is always the signed-in employee, never anything from the request body.
        turn = agent.send(thread_id=thread_id, requester_email=user.email, message=body.message)
    except ThreadNotFound:
        log.warning("conversation not found or not owned")
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    # Counts and labels only: what the employee or the agent said is never logged.
    log.info(
        "chat turn",
        extra={
            "fields": {
                "intent": turn.intent.intent.value if turn.intent else None,
                "intent_source": turn.intent.source if turn.intent else None,
                "workflow": turn.workflow.value,
                "awaiting": turn.awaiting.value if turn.awaiting else None,
                "sources": len(turn.sources),
                "ticket_key": turn.created_ticket.key if turn.created_ticket else None,
                "message_chars": len(body.message),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        },
    )
    return to_chat_response(turn, user.email)


@router.get("/{thread_id}", response_model=HistoryResponse)
def get_history(
    thread_id: UUID, request: Request, user: Annotated[User, Depends(current_user)]
) -> HistoryResponse:
    agent = _agent(request)
    bind_thread(str(thread_id), str(user.id))
    try:
        view = agent.history(thread_id=str(thread_id), requester_email=user.email)
    except ThreadNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    return to_history_response(view, user.email)
