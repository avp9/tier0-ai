from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from tier0.agent.service import (
    AgentService,
    ChatTurn,
    EditNotAllowed,
    NothingToEdit,
    ThreadNotFound,
    TokenEvent,
)
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
NOTHING_TO_EDIT = "There is no ticket to edit right now."
ALREADY_SENT = "This ticket has already been sent, so it can no longer be edited."


def _agent(request: Request) -> AgentService:
    agent: AgentService | None = request.app.state.agent
    if agent is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chat is not available right now")
    return agent


STREAM_FAILED = "Something went wrong. Please try again."
# Keep proxies from holding the stream back. (API responses already get Cache-Control: no-store.)
SSE_HEADERS = {"X-Accel-Buffering": "no"}


def _wants_stream(request: Request) -> bool:
    return "text/event-stream" in request.headers.get("accept", "")


def _log_turn(turn: ChatTurn, message: str, started: float) -> None:
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
                "message_chars": len(message),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        },
    )


def _event(name: str, data: object) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _record(request: Request, thread_id: str, email: str, message: str) -> None:
    """Add the chat to the employee's history. The history is a convenience: never fail a turn."""
    try:
        request.app.state.conversations.touch(thread_id, email, message)
    except Exception:
        log.warning("could not record the conversation in the history", exc_info=True)


def _sse(
    events: Iterator[TokenEvent | ChatTurn],
    email: str,
    message: str,
    started: float,
    on_done: Callable[[], None],
) -> Iterator[str]:
    """Server-sent events: ``token`` pieces as the reply forms, then ``done`` with the turn."""
    try:
        for item in events:
            if isinstance(item, TokenEvent):
                yield _event("token", {"text": item.text})
            else:
                _log_turn(item, message, started)
                on_done()
                yield _event("done", to_chat_response(item, email).model_dump(mode="json"))
    except Exception:
        log.exception("chat stream failed")
        yield _event("error", {"detail": STREAM_FAILED})


@router.post("", response_model=ChatResponse, dependencies=[Depends(same_origin)])
def send_message(
    body: ChatRequest, request: Request, user: Annotated[User, Depends(current_user)]
) -> ChatResponse | StreamingResponse:
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
    text = body.message or ""  # empty for a ticket edit, which carries no typed message
    # The requester is always the signed-in employee, never anything from the request body.
    try:
        if _wants_stream(request):
            events = agent.stream(
                thread_id=thread_id,
                requester_email=user.email,
                message=text,
                ticket_edit=body.ticket_edit,
            )
            return StreamingResponse(
                _sse(
                    events,
                    user.email,
                    text,
                    started,
                    lambda: _record(request, thread_id, user.email, text),
                ),
                media_type="text/event-stream",
                headers=SSE_HEADERS,
            )
        turn = agent.send(
            thread_id=thread_id,
            requester_email=user.email,
            message=text,
            ticket_edit=body.ticket_edit,
        )
    except ThreadNotFound:
        log.warning("conversation not found or not owned")
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    except NothingToEdit:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_TO_EDIT) from None
    except EditNotAllowed:
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_SENT) from None
    _log_turn(turn, text, started)
    _record(request, thread_id, user.email, text)
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
