from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from tier0.api.deps import current_user
from tier0.auth.users import User

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


class ConversationItem(BaseModel):
    thread_id: str
    title: str
    updated_at: datetime


@router.get("", response_model=list[ConversationItem])
def list_conversations(
    request: Request, user: Annotated[User, Depends(current_user)]
) -> list[ConversationItem]:
    """The signed-in employee's chats, newest first, for the history sidebar. Only their own."""
    chats = request.app.state.conversations.recent(user.email)
    return [
        ConversationItem(thread_id=c.thread_id, title=c.title, updated_at=c.updated_at)
        for c in chats
    ]
