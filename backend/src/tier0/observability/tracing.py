"""LangSmith tracing of chat turns, with personal details removed before anything leaves.

One trace per message the employee sends: the agent's steps (intent, rewrite, cache, search,
model calls, ticket drafting and the ticket system) as a tree, grouped by conversation. It is
controlled by this application's own settings, never by ambient environment variables, so what is
sent is always the redacted version, and each environment writes to its own project.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
import os
import re
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from langsmith import Client, agent_address, traceable
from langsmith import utils as ls_utils
from langsmith.run_helpers import get_current_run_tree

from tier0.observability.redact import redact

if TYPE_CHECKING:
    from tier0.config import Settings

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# APP_ENV -> the environment half of the project name. The test stack (e2e) never traces.
ENVIRONMENTS = {"local": "local", "staging": "staging", "production": "production"}

MAX_TEXT_CHARS = 4000
MAX_ITEMS = 30
# Kept out of traces whatever they hold: who is asking, and anything secret-shaped.
DROPPED_KEYS = frozenset(
    {"requester_email", "email", "api_key", "password", "authorization", "token", "secret"}
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_NUMBER = re.compile(r"\d(?:[ -]?\d){8,}")  # a phone, card or account number


def scrub_text(text: str) -> str:
    """Mask email addresses and long numbers, then anything secret-shaped, and cap the length."""
    text = _EMAIL.sub("[email]", text)
    text = _LONG_NUMBER.sub("[number]", text)
    text = redact(text)
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS] + " […]"


def scrub(value: Any, _depth: int = 0) -> Any:
    """A copy of ``value`` that is safe to send: personal details masked, long things cut short."""
    if _depth > 8:
        return "[…]"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, dict):
        return {
            str(key): "[removed]" if str(key).lower() in DROPPED_KEYS else scrub(item, _depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        items = list(value)[-MAX_ITEMS:]  # the most recent ones
        return [scrub(item, _depth + 1) for item in items]
    if hasattr(value, "model_dump"):
        return scrub(value.model_dump(mode="json"), _depth + 1)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return scrub(dataclasses.asdict(value), _depth + 1)
    return scrub_text(str(value))


def scrub_io(data: dict[str, Any]) -> dict[str, Any]:
    """The hook the LangSmith client calls on every run's inputs, outputs and metadata."""
    cleaned = scrub(data)
    return cleaned if isinstance(cleaned, dict) else {"value": cleaned}


def project_name(agent_id: str, environment: str) -> str:
    return f"{agent_id}-{environment}"


def disable_ambient_tracing() -> None:
    """Stop the SDK tracing because an environment variable says so (LANGSMITH_TRACING in a .env
    file, say). Tracing here is switched on only by this application's settings, through a client
    that redacts, so an ambient tracer would send the same turn twice, unredacted."""
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2"):
        os.environ[name] = "false"
    clear = getattr(ls_utils.get_env_var, "cache_clear", None)
    if clear is not None:  # the SDK remembers what it read first
        clear()


def traced(**options: Any) -> Callable[[F], F]:
    """Record a call as a step of the current turn, and do nothing outside one.

    Unlike ``langsmith.traceable`` this never starts a trace of its own: scripts and tests that
    call the same code, with tracing on or off, never produce stray single-step traces.
    """

    def decorate(function: F) -> F:
        wrapped = traceable(**options)(function)

        @functools.wraps(function)
        def inner(*args: Any, **kwargs: Any) -> Any:
            if get_current_run_tree() is None:
                return function(*args, **kwargs)
            return wrapped(*args, **kwargs)

        return inner  # type: ignore[return-value]

    return decorate


def inside_a_turn() -> bool:
    """Whether a trace is being recorded right now."""
    return get_current_run_tree() is not None


def note(**metadata: Any) -> None:
    """Attach facts to the step being recorded (for example whether the cache was used)."""
    run = get_current_run_tree()
    if run is not None:
        run.add_metadata(scrub(metadata))


def drop_self(inputs: dict[str, Any]) -> dict[str, Any]:
    """For ``process_inputs`` of a method: the arguments without the object they belong to."""
    return {k: v for k, v in inputs.items() if k not in ("self", "cls")}


class Tracing:
    """What the agent needs to record a turn, and to attach an employee's vote to it."""

    def __init__(
        self, client: Client, project: str, environment: str, agent_id: str | None = None
    ) -> None:
        self._client = client
        self.project = project
        self.environment = environment
        # Names the project for feedback, so no lookup is needed (the SDK's own recommendation).
        self._address = agent_address(agent_id, environment) if agent_id else None

    def config(self, *, thread_id: str, user_id: str | None) -> dict[str, Any]:
        """Settings for one graph run: its tracer, name, grouping by conversation, and labels."""
        from langchain_core.tracers.langchain import LangChainTracer

        return {
            "callbacks": [LangChainTracer(client=self._client, project_name=self.project)],
            "run_name": "chat_turn",
            "tags": ["tier0", f"env:{self.environment}"],
            "metadata": {
                "thread_id": thread_id,  # LangSmith groups runs with the same one into a thread
                "user_id": user_id,
                "environment": self.environment,
            },
        }

    def submit_feedback(
        self,
        run_id: str,
        score: float | None,
        comment: str | None = None,
        *,
        replace: bool = False,
    ) -> None:
        """Record the employee's vote on a turn: a new one, a changed one (``replace``), or with
        ``score`` None a withdrawn one. Never raises: a feedback problem is not worth a failed
        request."""
        feedback_id = uuid.uuid5(uuid.NAMESPACE_URL, f"tier0-feedback:{run_id}")
        cleaned = scrub_text(comment) if comment else None
        try:
            if score is None or replace:
                # The first vote uploads in the background; make sure it has landed before it is
                # changed or withdrawn, or the change would find nothing to change.
                self._client.flush()
            if score is None:
                self._client.delete_feedback(feedback_id)
            elif replace:
                self._client.update_feedback(feedback_id, score=score, comment=cleaned)
            else:
                self._client.create_feedback(
                    run_id,
                    key="user_score",
                    score=score,
                    comment=cleaned,
                    feedback_id=feedback_id,
                    trace_id=run_id,  # the turn is the whole trace: uploads in the background
                    address=self._address,
                    source_info={"source": "tier0-ui"},
                )
        except Exception as error:
            log.warning("could not record feedback in LangSmith: %s", type(error).__name__)

    def flush(self) -> None:
        """Send anything still queued (at shutdown)."""
        try:
            self._client.flush()
        except Exception as error:  # pragma: no cover - best effort
            log.warning("could not flush traces: %s", type(error).__name__)


def build_tracing(
    settings: Settings, client_factory: Callable[..., Client] = Client
) -> Tracing | None:
    """The tracer for these settings, or None when tracing is off."""
    disable_ambient_tracing()
    if not settings.langsmith_tracing:
        return None
    environment = ENVIRONMENTS.get(settings.app_env)
    if environment is None:
        log.info("LangSmith tracing is never on for APP_ENV=%s", settings.app_env)
        return None
    if settings.langsmith_api_key is None:
        log.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is not set; not tracing")
        return None
    client = client_factory(
        api_url=settings.langsmith_endpoint,
        api_key=settings.langsmith_api_key.get_secret_value(),
        hide_inputs=scrub_io,
        hide_outputs=scrub_io,
        hide_metadata=scrub_io,
        tracing_error_callback=lambda error: log.warning(
            "LangSmith trace upload failed: %s", type(error).__name__
        ),
    )
    project = project_name(settings.langsmith_agent_id, environment)
    log.info("LangSmith tracing on", extra={"fields": {"project": project}})
    return Tracing(client, project, environment, settings.langsmith_agent_id)
