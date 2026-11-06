from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tier0.api.app import create_app
from tier0.api.static import CONTENT_SECURITY_POLICY
from tier0.config import Settings

CANARY = "TOP-CANARY-OUTSIDE-THE-BUILD"
INDEX = "<!doctype html><title>Tier0</title><script type=module src=/assets/app-abc123.js></script>"


@pytest.fixture
def build(tmp_path: Path) -> Path:
    """A fake build folder, with a secret file just outside it."""
    (tmp_path / "secret.txt").write_text(CANARY)
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "app-abc123.js").write_text("console.log('app')")
    (root / "assets" / "app-abc123.css").write_text("body{}")
    (root / "index.html").write_text(INDEX)
    (root / "favicon.svg").write_text("<svg/>")
    (root / "nested").mkdir()
    (root / "nested" / "inner.txt").write_text("inner")
    return root


def client_for(static_dir: Path | None, **overrides: Any) -> TestClient:
    # No `with`: the start-up (database, agent) is not needed to test file serving.
    kwargs: dict[str, Any] = {
        "database_url": "postgresql://u@localhost/none",
        "session_secret": "s" * 40,
        "static_dir": str(static_dir) if static_dir else None,
        **overrides,
    }
    settings = Settings(**kwargs)
    return TestClient(create_app(settings))


def test_the_home_page_is_the_app_with_a_strict_content_security_policy(build: Path) -> None:
    response = client_for(build).get("/")
    assert response.status_code == 200 and response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    policy = response.headers["content-security-policy"]
    assert policy == CONTENT_SECURITY_POLICY
    for directive in (
        "default-src 'none'",
        "script-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ):
        assert directive in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert response.headers["cache-control"] == "no-cache"
    assert (
        response.headers["x-content-type-options"] == "nosniff"
    )  # the general headers still apply


@pytest.mark.parametrize(
    "path", ["/login", "/chat/some/client/route", "/anything.html", "/nested/"]
)
def test_client_side_routes_get_the_app(build: Path, path: str) -> None:
    response = client_for(build).get(path)
    assert response.status_code == 200 and response.text == INDEX


def test_hashed_assets_are_cached_for_a_year(build: Path) -> None:
    response = client_for(build).get("/assets/app-abc123.js")
    assert response.status_code == 200 and response.text == "console.log('app')"
    assert "javascript" in response.headers["content-type"]
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client_for(build).get("/assets/app-abc123.css").status_code == 200


def test_a_missing_asset_is_a_404_not_the_app(build: Path) -> None:
    response = client_for(build).get("/assets/missing.js")
    assert response.status_code == 404 and "<title>" not in response.text


def test_the_favicon_and_other_top_level_files_are_served(build: Path) -> None:
    response = client_for(build).get("/favicon.svg")
    assert response.status_code == 200 and response.text == "<svg/>"
    assert "immutable" not in response.headers["cache-control"]


def test_only_files_present_at_start_up_can_be_served(build: Path) -> None:
    c = client_for(build)
    (build / "added-later.txt").write_text("late")
    assert (
        c.get("/added-later.txt").text == INDEX
    )  # not on the start-up list: the app, not the file


def test_the_page_itself_is_never_served_as_a_download(build: Path) -> None:
    response = client_for(build).get("/index.html")
    assert response.text == INDEX and "content-security-policy" in response.headers


def test_files_in_subfolders_are_not_exposed(build: Path) -> None:
    assert client_for(build).get("/nested/inner.txt").text == INDEX  # the app, not the file


@pytest.mark.parametrize(
    "path",
    [
        "/../secret.txt",
        "/assets/../../secret.txt",
        "/%2e%2e/secret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/..%2fsecret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/%252e%252e/secret.txt",
        "//secret.txt",
        "/assets/....//secret.txt",
    ],
)
def test_paths_cannot_escape_the_build_folder(build: Path, path: str) -> None:
    response = client_for(build).get(path)
    assert CANARY not in response.text


def test_there_is_no_directory_listing(build: Path) -> None:
    for path in ("/assets", "/assets/"):
        response = client_for(build).get(path, follow_redirects=True)
        assert "app-abc123" not in response.text or response.text == INDEX
        assert "Index of" not in response.text


def test_an_unknown_api_route_is_a_json_404_never_the_app(build: Path) -> None:
    c = client_for(build)
    for path in ("/api/nothing", "/api/chat/x/y/z", "/api"):
        response = c.get(path)
        assert response.status_code in (404, 422), path
        assert "<title>" not in response.text, path
    assert c.get("/api/nothing").json() == {"detail": "Not Found"}


def test_real_api_routes_still_win(build: Path) -> None:
    assert client_for(build).get("/api/health").json() == {"status": "ok"}


def test_pages_answer_get_and_head_only(build: Path) -> None:
    c = client_for(build)
    head = c.head("/")
    assert head.status_code == 200 and head.content == b""
    assert head.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert c.post("/").status_code == 405
    assert c.put("/").status_code == 405


def test_without_a_static_folder_nothing_extra_is_served() -> None:
    assert client_for(None).get("/").status_code == 404


def test_a_folder_without_an_app_is_a_warning_locally_and_an_error_when_deployed(
    tmp_path: Path,
) -> None:
    assert client_for(tmp_path).get("/").status_code == 404  # local: carries on without it
    with pytest.raises(FileNotFoundError, match="index.html"):
        client_for(tmp_path, app_env="production")


def test_the_real_build_has_no_inline_script_or_style_so_the_policy_will_not_break_it() -> None:
    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist" / "index.html"
    if not dist.is_file():
        pytest.skip("run `npm run build` in frontend/ to check the real build")
    html = dist.read_text()
    assert "<script>" not in html and "<style" not in html and "style=" not in html
    assert " onclick=" not in html and "javascript:" not in html
    assert 'type="module"' in html and "src=" in html
