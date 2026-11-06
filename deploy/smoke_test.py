"""Smoke tests for a deployed Tier0 environment. Standard library only.

  python deploy/smoke_test.py https://ca-tier0-staging.example.io

Reads SMOKE_EMAIL and SMOKE_PASSWORD (a demo user) from the environment. Optional:
  SMOKE_QUESTION      a question the knowledge base answers   (default: password reset)
  SMOKE_EXPECT_SOURCES=1  also require the answer to cite sources
  SMOKE_WAIT_SECONDS  how long to wait for a cold start        (default: 240)
Exits 0 only if every check passes. Never creates a ticket.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request


class Smoke:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.failures: list[str] = []

    def call(
        self, method: str, path: str, body: object | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - the URL is operator-supplied https
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Origin": self.base},
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(
            f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else "")
        )
        if not ok:
            self.failures.append(name)

    def wait_until_up(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                if self.call("GET", "/api/health")[0] == 200:
                    return True
            except OSError:
                pass
            time.sleep(5)
        return False


def run(base: str) -> int:
    email = os.environ.get("SMOKE_EMAIL", "")
    password = os.environ.get("SMOKE_PASSWORD", "")
    if not email or not password:
        print("SMOKE_EMAIL and SMOKE_PASSWORD must be set")
        return 2
    smoke = Smoke(base)
    wait = float(os.environ.get("SMOKE_WAIT_SECONDS", "240"))
    up = smoke.wait_until_up(wait)
    smoke.check("server answers /api/health (waits for a cold start)", up)
    if not up:
        return 1

    status, _, body = smoke.call("GET", "/api/ready")
    smoke.check(
        "ready: database, schema and knowledge index",
        status == 200,
        f"{status} {body[:200]!r}",
    )

    status, headers, body = smoke.call("GET", "/")
    csp = {k.lower(): v for k, v in headers.items()}.get("content-security-policy", "")
    smoke.check("web app is served", status == 200 and b'<div id="root"' in body, str(status))
    smoke.check(
        "strict content security policy",
        "script-src 'self'" in csp and "unsafe" not in csp,
        csp,
    )

    status, _, _ = smoke.call("POST", "/api/chat", {"message": "hello"})
    smoke.check("chat refuses an anonymous caller", status == 401, str(status))
    status, _, _ = smoke.call("GET", "/api/_e2e/tickets")
    smoke.check("test controls are not exposed", status == 404, str(status))
    status, _, _ = smoke.call(
        "POST", "/api/auth/login", {"email": email, "password": "wrong-" + password}
    )
    smoke.check("a wrong password is refused", status == 401, str(status))

    status, _, _ = smoke.call("POST", "/api/auth/login", {"email": email, "password": password})
    smoke.check("smoke user can sign in", status == 204, str(status))
    if status == 204:
        question = os.environ.get("SMOKE_QUESTION", "How do I reset my password?")
        status, _, raw = smoke.call("POST", "/api/chat", {"message": question})
        reply: dict[str, object] = json.loads(raw) if status == 200 else {}
        smoke.check(
            "a knowledge question gets a reply",
            status == 200 and bool(reply.get("reply")),
            str(status),
        )
        smoke.check(
            "answering did not create a ticket",
            reply.get("ticket") in (None, {}),
            str(reply.get("ticket")),
        )
        if os.environ.get("SMOKE_EXPECT_SOURCES") == "1":
            smoke.check("the answer cites sources", bool(reply.get("sources")))
        smoke.call("POST", "/api/auth/logout")

    print("\nALL PASSED" if not smoke.failures else f"\nFAILED: {', '.join(smoke.failures)}")
    return 1 if smoke.failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith(
        ("https://", "http://localhost", "http://127.0.0.1")
    ):
        sys.exit("usage: smoke_test.py https://<host>   (http only for localhost)")
    sys.exit(run(sys.argv[1]))
