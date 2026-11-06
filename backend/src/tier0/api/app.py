from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, Request, Response
from psycopg_pool import ConnectionPool

from tier0.agent.checkpoint import make_pool
from tier0.api.routes_auth import router as auth_router
from tier0.api.routes_health import database_check, migrations_check
from tier0.api.routes_health import router as health_router
from tier0.auth.ratelimit import LoginRateLimiter
from tier0.auth.sessions import SessionCodec
from tier0.auth.users import UserStore, seed_demo_users
from tier0.config import Settings
from tier0.db.migrate import upgrade_with_lock

HSTS = "max-age=31536000; includeSubDomains"


def create_app(settings: Settings, *, pool: ConnectionPool | None = None) -> FastAPI:
    """Build the web application. Pass ``pool`` to share one (the caller then closes it)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if settings.run_migrations_on_start:
            upgrade_with_lock(settings.database_url)
        own_pool = pool is None
        active = pool or make_pool(settings.database_url)
        try:
            users = UserStore(active)
            seed_demo_users(users, settings.demo_users)
            app.state.settings = settings
            app.state.pool = active
            app.state.users = users
            app.state.codec = SessionCodec(
                settings.session_secret.get_secret_value(), settings.session_max_age_hours * 3600
            )
            app.state.limiter = LoginRateLimiter(
                settings.login_max_failures_per_email,
                settings.login_max_failures_per_ip,
                settings.login_window_minutes * 60,
            )
            app.state.readiness_checks = {
                "database": partial(database_check, active),
                "migrations": partial(migrations_check, active),
            }
            yield
        finally:
            if own_pool:
                active.close()

    docs = {} if settings.is_local else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Tier0 AI", lifespan=lifespan, **docs)  # type: ignore[arg-type]

    @app.middleware("http")
    async def secure_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if settings.cookie_secure:
            response.headers["Strict-Transport-Security"] = HSTS
        return response

    app.include_router(health_router)
    app.include_router(auth_router)
    return app


def create_app_from_env() -> FastAPI:
    """Entry point for the server: ``uvicorn --factory tier0.api.app:create_app_from_env``."""
    return create_app(Settings())  # type: ignore[call-arg]
