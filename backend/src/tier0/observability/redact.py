from __future__ import annotations

import re

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_URL_PASSWORD = re.compile(r"(://[^:/\s@]+:)[^@\s]+(@)")
_API_KEY = re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
_ASSIGNED_SECRET = re.compile(
    r"""(?ix)\b((?:api[_-]?key|token|password|passwd|secret|authorization)\w*["']?\s*[:=]\s*["']?)
    [^\s"',;&]+"""
)
MAX_FIELD_CHARS = 300


def mask_email(match: re.Match[str]) -> str:
    return f"{match.group(1)}***@{match.group(2)}"


def redact(text: str) -> str:
    """Hide email addresses (keeping the first letter and domain) and anything secret-shaped."""
    text = _EMAIL.sub(mask_email, text)
    text = _URL_PASSWORD.sub(r"\1***\2", text)
    text = _API_KEY.sub("[redacted-key]", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", text)
    return _ASSIGNED_SECRET.sub(r"\1[redacted]", text)


def clean_value(value: object) -> object:
    """Make a structured field safe to log: redact and shorten text, leave numbers alone."""
    if isinstance(value, bool | int | float) or value is None:
        return value
    if isinstance(value, list | tuple):
        return [clean_value(item) for item in value][:20]
    text = redact(str(value))
    return text if len(text) <= MAX_FIELD_CHARS else text[: MAX_FIELD_CHARS - 1] + "…"
