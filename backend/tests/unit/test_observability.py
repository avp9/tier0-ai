import json
import logging
import re

from tier0.observability import context
from tier0.observability.logs import (
    CorrelationFilter,
    JsonFormatter,
    TextFormatter,
    configure_logging,
)
from tier0.observability.redact import MAX_FIELD_CHARS, clean_value, redact


def make_record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("tier0.test", logging.INFO, __file__, 1, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    CorrelationFilter().filter(record)
    return record


# --- redaction ------------------------------------------------------------------------


def test_emails_keep_only_the_first_letter_and_domain() -> None:
    assert redact("sent to alice@buzztech.example today") == "sent to a***@buzztech.example today"
    assert redact("a.b+c@x.co and d@y.org") == "a***@x.co and d***@y.org"


def test_secrets_are_hidden() -> None:
    assert "sk-abcdefghijklmnop1234" not in redact("key sk-abcdefghijklmnop1234 used")
    assert "abc123tokenvalue" not in redact("Authorization: Bearer abc123tokenvalue")
    assert "hunter2" not in redact("password=hunter2&x=1")
    assert "hunter2" not in redact('{"api_key": "hunter2"}')
    assert redact("postgresql://tier0admin:S3cretPw@db.example:5432/x") == (
        "postgresql://tier0admin:***@db.example:5432/x"
    )


def test_ordinary_text_is_left_alone() -> None:
    text = "chat turn intent=KNOWLEDGE_QUESTION sources=3 took 120 ms for SUP-42"
    assert redact(text) == text


def test_clean_value_shortens_text_and_keeps_numbers() -> None:
    assert clean_value(5) == 5
    assert clean_value(None) is None
    assert clean_value(True) is True
    long = clean_value("x" * 1000)
    assert isinstance(long, str) and len(long) == MAX_FIELD_CHARS
    assert clean_value(["a@b.co", 2]) == ["a***@b.co", 2]


# --- formatting -----------------------------------------------------------------------


def test_json_lines_carry_correlation_ids_and_cleaned_fields() -> None:
    ctx = context.start_request("req-abcdefgh-1234")
    context.bind_thread("thread-1", "user-9")
    record = make_record("signed in alice@buzztech.example", fields={"email": "bob@x.org", "n": 3})
    line = JsonFormatter().format(record)
    event = json.loads(line)
    assert event["request_id"] == ctx.request_id == "req-abcdefgh-1234"
    assert (event["thread_id"], event["user_id"]) == ("thread-1", "user-9")
    assert event["message"] == "signed in a***@buzztech.example"
    assert (event["email"], event["n"]) == ("b***@x.org", 3)
    assert "alice@" not in line and "bob@" not in line
    assert re.match(r"\d{4}-\d\d-\d\dT", event["ts"])


def test_fields_cannot_overwrite_core_keys() -> None:
    event = json.loads(JsonFormatter().format(make_record("real", fields={"message": "forged"})))
    assert event["message"] == "real"


def test_exceptions_are_included_and_redacted() -> None:
    try:
        raise ValueError("bad value for alice@buzztech.example")
    except ValueError:
        import sys

        record = make_record("failed")
        record.exc_info = sys.exc_info()
    event = json.loads(JsonFormatter().format(record))
    assert "ValueError" in event["exception"]
    assert "alice@" not in event["exception"]


def test_text_format_is_readable_and_redacted() -> None:
    context.start_request("req-abcdefgh-5678")
    line = TextFormatter().format(make_record("hi a@b.co", fields={"n": 1}))
    assert "hi a***@b.co" in line and "request_id=req-abcdefgh-5678" in line and "n=1" in line


# --- request IDs ----------------------------------------------------------------------


def test_only_plain_ids_are_accepted_from_callers() -> None:
    assert context.new_request_id("abc12345-def") == "abc12345-def"
    for bad in (None, "", "short", "has space in it 1234", "line\nbreak-12345", "x" * 65):
        generated = context.new_request_id(bad)
        assert generated != bad and len(generated) == 36


def test_no_context_means_no_ids() -> None:
    context._current.set(None)
    event = json.loads(JsonFormatter().format(make_record("outside a request")))
    assert "request_id" not in event


def test_configure_logging_is_idempotent() -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging("INFO")
        configure_logging("WARNING", json_lines=False)
        assert len(root.handlers) == 1
        assert root.level == logging.WARNING
        assert logging.getLogger("uvicorn.access").propagate is False
    finally:
        root.handlers[:], root.level = saved[0], saved[1]
