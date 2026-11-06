from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def quote(value: str) -> str:
    """Render a value as a JQL string literal; no input can end the literal early."""
    if _CONTROL.search(value):
        raise ValueError("control characters are not allowed in a JQL value")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
