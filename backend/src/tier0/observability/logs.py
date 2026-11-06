from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from tier0.observability import context
from tier0.observability.redact import clean_value, redact

# Fields a log call may attach with ``extra={"fields": {...}}``.
FIELDS_ATTRIBUTE = "fields"


class CorrelationFilter(logging.Filter):
    """Stamp every record with the current request and conversation IDs."""

    def filter(self, record: logging.LogRecord) -> bool:
        current = context.current()
        record.request_id = current.request_id if current else None
        record.thread_id = current.thread_id if current else None
        record.user_id = current.user_id if current else None
        return True


def _event(record: logging.LogRecord) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
        "level": record.levelname,
        "logger": record.name,
        "message": redact(record.getMessage()),
    }
    for name in ("request_id", "thread_id", "user_id"):
        value = getattr(record, name, None)
        if value:
            event[name] = value
    fields = getattr(record, FIELDS_ATTRIBUTE, None)
    if isinstance(fields, dict):
        for key, value in fields.items():
            event.setdefault(str(key), clean_value(value))
    if record.exc_info and record.exc_info[0] is not None:
        formatted = logging.Formatter().formatException(record.exc_info)
        event["exception"] = redact(formatted)
    return event


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_event(record), ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Readable lines for local development; still redacted and still correlated."""

    def format(self, record: logging.LogRecord) -> str:
        event = _event(record)
        extras = " ".join(
            f"{k}={v}"
            for k, v in event.items()
            if k not in ("ts", "level", "logger", "message", "exception")
        )
        line = f"{event['ts']} {event['level']:<7} {event['logger']}: {event['message']}"
        if extras:
            line += f"  [{extras}]"
        return line + (f"\n{event['exception']}" if "exception" in event else "")


def configure_logging(level: str = "INFO", *, json_lines: bool = True) -> None:
    """Send all logs, including uvicorn's, to stdout in one format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_lines else TextFormatter())
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    # The request middleware writes one access line per request, with the correlation ID.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs of model calls
