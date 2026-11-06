from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from tier0.auth.sessions import COOKIE_NAME
from tier0.auth.users import User


def current_user(request: Request) -> User:
    """The signed-in employee, or 401. Looked up on every request, so a removed user is out."""
    token = request.cookies.get(COOKIE_NAME)
    user_id = request.app.state.codec.read(token) if token else None
    user: User | None = request.app.state.users.get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def same_origin(request: Request) -> None:
    """Refuse browser requests that come from another site (a CSRF guard on top of SameSite)."""
    origin = request.headers.get("origin")
    if origin is not None and urlparse(origin).netloc != request.headers.get("host", ""):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-origin request refused")
