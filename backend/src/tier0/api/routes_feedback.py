from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, StringConstraints

from tier0.api.deps import current_user, same_origin
from tier0.auth.users import User
from tier0.feedback import read_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feedback", tags=["feedback"])

MAX_COMMENT_CHARS = 500
UNKNOWN_ANSWER = "That answer is not one this account was given."


class FeedbackRequest(BaseModel):
    token: Annotated[str, StringConstraints(min_length=36, max_length=120)]
    vote: Literal["up", "down", "none"]  # "none" takes a vote back
    comment: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=MAX_COMMENT_CHARS)]
        | None
    ) = Field(default=None)


class FeedbackResponse(BaseModel):
    vote: Literal["up", "down", "none"]


@router.post("", response_model=FeedbackResponse, dependencies=[Depends(same_origin)])
def give_feedback(
    body: FeedbackRequest,
    request: Request,
    background: BackgroundTasks,
    user: Annotated[User, Depends(current_user)],
) -> FeedbackResponse:
    """Record a thumbs up or down on an answer this person was given (or take it back)."""
    secret = request.app.state.settings.session_secret.get_secret_value()
    trace_id = read_token(secret, body.token, user.email)
    if trace_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_ANSWER)
    store = request.app.state.feedback
    tracing = request.app.state.tracing
    already_voted = store.get(trace_id, user.email) is not None
    if body.vote == "none":
        store.clear(trace_id, user.email)
        score: float | None = None
    else:
        store.save(trace_id, user.email, good=body.vote == "up", comment=body.comment)
        score = 1.0 if body.vote == "up" else 0.0
    if tracing is not None:  # to LangSmith, after the reply: its being slow is not their problem
        background.add_task(
            tracing.submit_feedback, trace_id, score, body.comment, replace=already_voted
        )
    log.info("feedback recorded", extra={"fields": {"vote": body.vote}})
    return FeedbackResponse(vote=body.vote)
