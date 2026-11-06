from __future__ import annotations

import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass

# An ID supplied by a caller is only trusted if it is plainly an ID: it ends up in every log line.
_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


@dataclass
class RequestContext:
    """Correlation IDs for the request being handled (shared, so a route can add the thread)."""

    request_id: str
    thread_id: str | None = None
    user_id: str | None = None


_current: ContextVar[RequestContext | None] = ContextVar("tier0_request_context", default=None)


def new_request_id(supplied: str | None) -> str:
    return supplied if supplied and _SAFE_ID.match(supplied) else str(uuid.uuid4())


def start_request(supplied_id: str | None = None) -> RequestContext:
    context = RequestContext(request_id=new_request_id(supplied_id))
    _current.set(context)
    return context


def current() -> RequestContext | None:
    return _current.get()


def bind_thread(thread_id: str, user_id: str | None = None) -> None:
    """Record the conversation being served so every later log line carries it."""
    context = _current.get()
    if context is not None:
        context.thread_id = thread_id
        if user_id is not None:
            context.user_id = user_id
