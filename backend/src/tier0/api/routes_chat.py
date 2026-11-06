from __future__ import annotations

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
    wait = request.app.state.chat_limiter.hit(user.id)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "You're sending messages too quickly. Please wait a moment.",
            headers={"Retry-After": str(wait)},
        )
    thread_id = str(body.thread_id or uuid4())
    try:
        # The requester is always the signed-in employee, never anything from the request body.
        turn = agent.send(thread_id=thread_id, requester_email=user.email, message=body.message)
    except ThreadNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    return to_chat_response(turn, user.email)


@router.get("/{thread_id}", response_model=HistoryResponse)
def get_history(
    thread_id: UUID, request: Request, user: Annotated[User, Depends(current_user)]
) -> HistoryResponse:
    agent = _agent(request)
    try:
        view = agent.history(thread_id=str(thread_id), requester_email=user.email)
    except ThreadNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None
    return to_history_response(view, user.email)
