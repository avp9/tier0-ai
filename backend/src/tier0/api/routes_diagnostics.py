from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from tier0.api.deps import current_user
from tier0.auth.users import User
from tier0.rag.diagnostics import (
    EvaluationDataMissing,
    RetrievalDiagnostics,
    RetrievalReportOut,
    TooSoon,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

UNAVAILABLE = "The search check is not available right now."
NO_DATA = "The evaluation questions are not part of this deployment."
FAILED = "The search check could not be run. Please try again in a moment."


@router.get("/retrieval", response_model=RetrievalReportOut)
def retrieval_report(
    request: Request,
    _user: Annotated[User, Depends(current_user)],
    refresh: bool = False,
) -> RetrievalReportOut:
    """How well the knowledge-base search finds the right article (the evaluation report).

    A recent result is returned as it is; ``refresh=true`` runs the check again, but no more
    often than every half minute because each run calls the embedding service.
    """
    diagnostics: RetrievalDiagnostics | None = request.app.state.diagnostics
    if diagnostics is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, UNAVAILABLE)
    if not diagnostics.available:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_DATA)
    try:
        return diagnostics.report(refresh=refresh)
    except TooSoon as soon:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"The check was run a moment ago. Try again in {soon.retry_after} seconds.",
            headers={"Retry-After": str(soon.retry_after)},
        ) from None
    except EvaluationDataMissing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_DATA) from None
    except Exception:
        log.exception("retrieval check failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, FAILED) from None
