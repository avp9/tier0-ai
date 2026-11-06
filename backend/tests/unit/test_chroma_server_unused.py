"""Guards the premise of the CI audit exception for the chromadb server advisories (ADR 0002)."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DIRS = [REPO_ROOT / "backend" / "src", REPO_ROOT / "backend" / "scripts"]
DOCKER_AND_COMPOSE = [*REPO_ROOT.glob("Dockerfile*"), *REPO_ROOT.glob("docker-compose*.y*ml")]

SERVER_USAGE = re.compile(
    r"\b(HttpClient|AsyncHttpClient|CloudClient|trust_remote_code|chroma\s+run|chromadb\.server"
    r"|SimpleRBACAuthorizationProvider|chroma_server_host)\b"
)


def test_application_code_uses_only_the_embedded_chroma_client() -> None:
    offenders: list[str] = []
    for directory in SOURCE_DIRS:
        for path in directory.rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if SERVER_USAGE.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}: {line.strip()}")
    assert not offenders, offenders


def test_no_container_runs_a_chroma_server() -> None:
    for path in DOCKER_AND_COMPOSE:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"chromadb/chroma|chroma\s+run", text), path.name
