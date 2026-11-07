"""A LangSmith client that sends nothing over the network and keeps what it would have sent."""

from __future__ import annotations

import json
from typing import Any

import requests
from langsmith import Client

from tier0.config import Settings
from tier0.observability.tracing import Tracing, build_tracing


class _Response:
    status_code = 200
    headers: dict[str, str] = {}
    text = "{}"
    content = b"{}"
    ok = True
    reason = "OK"

    def json(self) -> dict[str, Any]:
        return {}

    def raise_for_status(self) -> None:
        return None


class CapturingSession(requests.Session):
    """Every request the client makes: (method, path, body)."""

    def __init__(self, fail: Exception | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, Any]] = []
        self.fail = fail

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        body = kwargs.get("data") or kwargs.get("json")
        if isinstance(body, bytes | bytearray):
            try:
                body = json.loads(body)
            except ValueError:
                body = None
        self.calls.append((method, url.replace("http://langsmith.test", ""), body))
        if self.fail is not None:
            raise self.fail
        return _Response()

    # --- reading back what was sent ---
    def runs(self) -> list[dict[str, Any]]:
        """Each run, as it was created."""
        return [b for m, u, b in self.calls if m == "POST" and u == "/runs" and isinstance(b, dict)]

    def run_updates(self) -> list[dict[str, Any]]:
        """Each run, as it was finished."""
        return [b for m, u, b in self.calls if m == "PATCH" and u.startswith("/runs/")]

    def everything_sent(self) -> str:
        return json.dumps(self.calls, default=str)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://x:y@localhost/z",
        "session_secret": "s" * 40,
        "langsmith_tracing": True,
        "langsmith_api_key": "ls-test-key",
        "langsmith_endpoint": "http://langsmith.test",
        "app_env": "local",
    }
    return Settings(**{**base, **overrides})


def make_tracing(
    session: CapturingSession | None = None, **overrides: Any
) -> tuple[Tracing, CapturingSession]:
    session = session or CapturingSession()

    def factory(**kwargs: Any) -> Client:
        return Client(
            session=session,
            auto_batch_tracing=False,  # one request per run, so a test can read each one
            info={"version": "0.20.0", "batch_ingest_config": {}, "instance_flags": {}},
            **kwargs,
        )

    tracing = build_tracing(make_settings(**overrides), client_factory=factory)
    assert tracing is not None
    return tracing, session
