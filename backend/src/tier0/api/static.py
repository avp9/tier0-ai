from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

# The built app has no inline script or style, so nothing needs 'unsafe-inline'.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ]
)
IMMUTABLE = "public, max-age=31536000, immutable"


class HashedAssets(StaticFiles):
    """Vite names these files by content, so browsers may keep them forever."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = IMMUTABLE
        return response


def frontend_available(directory: Path) -> bool:
    return (directory / "index.html").is_file()


def mount_frontend(app: FastAPI, directory: Path) -> None:
    """Serve the built single-page app: assets directly, everything else as ``index.html``."""
    root = directory.resolve()
    index = root / "index.html"
    if (root / "assets").is_dir():
        app.mount("/assets", HashedAssets(directory=root / "assets"), name="assets")

    # An allowlist, fixed at start-up: a request is served a file only if its text is exactly one
    # of these names. No file path is ever built from what the browser sent.
    top_level = {
        entry.name: entry for entry in root.iterdir() if entry.is_file() and entry != index
    }
    page_headers = {"Content-Security-Policy": CONTENT_SECURITY_POLICY, "Cache-Control": "no-cache"}

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD"], include_in_schema=False, response_model=None
    )
    def single_page_app(path: str) -> Any:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Not Found")  # an unknown API route is never an HTML page
        known_file = top_level.get(path)
        if known_file is not None:
            return FileResponse(known_file, headers={"Cache-Control": "public, max-age=3600"})
        return FileResponse(index, headers=page_headers)
