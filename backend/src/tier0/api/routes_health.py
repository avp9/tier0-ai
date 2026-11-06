from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from tier0.db.migrate import head_revision

router = APIRouter(prefix="/api", tags=["health"])
log = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    """The process is up (container liveness). Deliberately checks nothing else."""
    return {"status": "ok"}


@router.get("/ready", response_model=None)
def ready(request: Request) -> JSONResponse | dict[str, str]:
    """Ready to serve: every registered check passes. Failure details stay in the logs."""
    failing = []
    for name, check in request.app.state.readiness_checks.items():
        try:
            healthy = check()
        except Exception:
            log.exception("readiness check %s raised", name)
            healthy = False
        if not healthy:
            failing.append(name)
    if failing:
        return JSONResponse({"status": "not_ready", "failing": failing}, status_code=503)
    return {"status": "ready"}


def database_check(pool: ConnectionPool) -> bool:
    with pool.connection() as connection:
        row = cast("dict[str, Any] | None", connection.execute("SELECT 1 AS ok").fetchone())
    return row is not None and row["ok"] == 1


def migrations_check(pool: ConnectionPool) -> bool:
    """The schema is at the revision this build expects (a deploy that skipped it is not ready)."""
    with pool.connection() as connection:
        row = cast(
            "dict[str, Any] | None",
            connection.execute("SELECT version_num FROM alembic_version").fetchone(),
        )
    return row is not None and row["version_num"] == head_revision()
