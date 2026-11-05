"""Generate the small deterministic knowledge base used by CI and e2e tests.

Run: uv run python scripts/build_fixture_kb.py
Selection is derived from the real knowledge base, so fixture ids and content match it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tier0.rag.loader import load_knowledge_base
from tier0.rag.schema import KnowledgeRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
KB_DIR = REPO_ROOT / "data" / "knowledge_base"
OUTPUT = REPO_ROOT / "data" / "fixtures" / "kb_fixture.json"

LABEL_ONLY_ANSWER = re.compile(r"Answer: Ticket category: [^\n]*$")
IT_TOPICS = {
    "password": r"password",
    "vpn": r"\bvpn\b",
    "mfa": r"multi-factor|\bmfa\b|two-factor|authenticator",
    "wifi": r"wi-?fi",
    "outlook": r"outlook",
    "software-install": r"install.{0,30}software|software.{0,30}install",
    "printer": r"printer",
    "network-drive": r"network drive|shared drive",
}
PER_IT_TOPIC = 3
BORROWED_HR_COUNT = 8
MAX_CHARS = 700


def has_real_answer(record: KnowledgeRecord) -> bool:
    return not LABEL_ONLY_ANSWER.search(record.content)


def select(records: list[KnowledgeRecord]) -> list[KnowledgeRecord]:
    by_size = sorted(records, key=lambda r: (len(r.content), r.document_id))
    chosen: dict[str, KnowledgeRecord] = {}

    it = [r for r in by_size if r.category == "IT Support" and has_real_answer(r)]
    for pattern in IT_TOPICS.values():
        rx = re.compile(pattern, re.IGNORECASE)
        hits = [r for r in it if rx.search(r.title) and len(r.content) <= MAX_CHARS]
        for record in [r for r in hits if r.document_id not in chosen][:PER_IT_TOPIC]:
            chosen[record.document_id] = record

    borrowed = [r for r in by_size if r.category == "HR Policy" and not r.synthetic]
    for record in borrowed[:BORROWED_HR_COUNT]:
        chosen[record.document_id] = record

    synthetic = sorted((r for r in records if r.synthetic), key=lambda r: r.document_id)
    for record in synthetic[::2]:
        chosen[record.document_id] = record

    return sorted(chosen.values(), key=lambda r: r.document_id)


def main() -> None:
    selected = select(load_knowledge_base(KB_DIR))
    OUTPUT.write_text(
        json.dumps([r.model_dump(mode="json") for r in selected], indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(selected)} records to {OUTPUT}")


if __name__ == "__main__":
    main()
