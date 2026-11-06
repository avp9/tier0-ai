from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from tier0.api.deps import current_user, same_origin
from tier0.auth.passwords import MAX_PASSWORD_CHARS, spend_verification_time, verify_password
from tier0.auth.sessions import COOKIE_NAME
from tier0.auth.users import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_LOGIN = "Invalid email or password"


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=MAX_PASSWORD_CHARS)


class Me(BaseModel):
    email: str
    display_name: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(same_origin)])
def login(body: LoginRequest, request: Request) -> Response:
    state = request.app.state
    email, ip = body.email.strip().lower(), _client_ip(request)

    wait = state.limiter.retry_after(email, ip)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many attempts. Please try again later.",
            headers={"Retry-After": str(wait)},
        )

    found = state.users.find_with_hash(email)
    if found is None:
        spend_verification_time(body.password)
        valid = False
    else:
        valid = verify_password(found[1], body.password)
    if not valid or found is None:
        state.limiter.record_failure(email, ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)

    state.limiter.record_success(email)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.set_cookie(
        COOKIE_NAME,
        state.codec.issue(found[0].id),
        max_age=state.codec.max_age_seconds,
        httponly=True,
        secure=state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(same_origin)])
def logout(request: Request) -> Response:
    """Always succeeds, so an expired or stale cookie can still be cleared."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
    )
    return response


@router.get("/me", response_model=Me)
def me(user: Annotated[User, Depends(current_user)]) -> Me:
    return Me(email=user.email, display_name=user.display_name)
