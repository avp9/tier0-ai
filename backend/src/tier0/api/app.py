from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from tier0.agent.checkpoint import make_pool
from tier0.agent.factory import BuiltAgent, ChatNotConfigured, build_agent_from_settings
from tier0.agent.service import AgentService
from tier0.api.routes_auth import router as auth_router
from tier0.api.routes_chat import router as chat_router
from tier0.api.routes_e2e import router as e2e_router
from tier0.api.routes_health import database_check, migrations_check
from tier0.api.routes_health import router as health_router
from tier0.api.static import frontend_available, mount_frontend
from tier0.auth.ratelimit import LoginRateLimiter, SlidingWindowLimiter
from tier0.auth.sessions import SessionCodec
from tier0.auth.users import UserStore, seed_demo_users
from tier0.config import Settings
from tier0.db.migrate import upgrade_with_lock
from tier0.observability.context import start_request
from tier0.observability.logs import configure_logging
from tier0.rag.indexer import IndexNotFoundError
from tier0.rag.retriever import IndexMismatchError

log = logging.getLogger(__name__)

HSTS = "max-age=31536000; includeSubDomains"


def create_app(
    settings: Settings,
    *,
    pool: ConnectionPool | None = None,
    agent: AgentService | None = None,
) -> FastAPI:
    """Build the web application.

    Pass ``pool`` to share one (the caller then closes it) and ``agent`` to use a ready-made
    agent instead of building the real one from the settings.
    """

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
            app.state.chat_limiter = SlidingWindowLimiter(settings.chat_messages_per_minute, 60)
            checks: dict[str, Callable[[], bool]] = {
                "database": partial(database_check, active),
                "migrations": partial(migrations_check, active),
            }
            app.state.agent = agent
            app.state.e2e_controls = None
            if agent is None:
                built = _build_agent(settings, active)
                app.state.agent = built.service if built else None
                if built is not None:
                    app.state.e2e_controls = built.controls
                    checks["knowledge_index"] = built.index_check
            app.state.readiness_checks = checks
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

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Give every request a correlation ID and write one access line (never the query)."""
        ctx = start_request(request.headers.get("X-Request-ID"))
        request.state.request_id = ctx.request_id  # for the error handler, which runs outside
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = ctx.request_id
            return response
        finally:
            if request.url.path != "/api/health":  # probes would drown everything else
                log.info(
                    "request",
                    extra={
                        "fields": {
                            "method": request.method,
                            "path": request.url.path,
                            "status": status_code,
                            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                        }
                    },
                )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        # This runs outside the request middleware, so the ID is read from the request itself.
        request_id = getattr(request.state, "request_id", None)
        log.exception(
            "unhandled error on %s %s",
            request.method,
            request.url.path,
            extra={"fields": {"request_id": request_id}},
        )
        headers = {"X-Request-ID": request_id} if request_id else {}
        return JSONResponse(
            {"detail": "Something went wrong. Please try again."}, status_code=500, headers=headers
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    if settings.e2e_controls_enabled:
        app.include_router(e2e_router)
    _serve_frontend(app, settings)  # last: its catch-all route must not shadow the API
    return app


def _serve_frontend(app: FastAPI, settings: Settings) -> None:
    if not settings.static_dir:
        return
    directory = Path(settings.static_dir)
    if frontend_available(directory):
        mount_frontend(app, directory)
    elif settings.is_local:
        log.warning("STATIC_DIR %s has no index.html; the web app is not served", directory)
    else:
        raise FileNotFoundError(f"STATIC_DIR {directory} has no index.html (was the app built?)")


def _build_agent(settings: Settings, pool: ConnectionPool) -> BuiltAgent | None:
    """Build the agent. Outside local development a misconfigured server refuses to start."""
    try:
        return build_agent_from_settings(settings, pool)
    except (ChatNotConfigured, IndexNotFoundError, IndexMismatchError) as error:
        if not settings.is_local:
            raise
        log.warning("chat is disabled for local development: %s", error)
        return None


def create_app_from_env() -> FastAPI:
    """Entry point for the server: ``uvicorn --factory tier0.api.app:create_app_from_env``."""
    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings.log_level, json_lines=settings.log_json)
    return create_app(settings)
