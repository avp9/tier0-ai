from __future__ import annotations

import ipaddress
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


def client_ip(request: Request) -> str:
    """The caller's address, believing X-Forwarded-For only as far as configured.

    With N trusted proxies the address is the Nth from the right: each proxy appends the peer it
    saw, so entries to the left of that may be forged. Anything that is not an IP address, or a
    header shorter than N, falls back to the direct peer.
    """
    peer = request.client.host if request.client else "unknown"
    hops = request.app.state.settings.trusted_proxy_hops
    if hops == 0:
        return peer
    entries = [
        e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()
    ]
    if len(entries) < hops:
        return peer
    try:
        return str(ipaddress.ip_address(entries[-hops]))
    except ValueError:
        return peer
