from collections import Counter
from pathlib import Path

from tier0.rag.loader import load_knowledge_base

KB_DIR = Path(__file__).resolve().parents[3] / "data" / "knowledge_base"


def test_full_knowledge_base_loads_and_ids_are_unique() -> None:
    records = load_knowledge_base(KB_DIR)
    assert len(records) > 40_000
    assert len({r.document_id for r in records}) == len(records)


def test_every_category_is_present_and_synthetic_content_is_flagged() -> None:
    records = load_knowledge_base(KB_DIR)
    categories = Counter(r.category for r in records)
    assert set(categories) == {"IT Support", "HR Policy", "Onboarding", "Payroll & Compensation"}
    synthetic = [r for r in records if r.synthetic]
    assert len(synthetic) >= 50
    assert all(r.last_updated is not None for r in synthetic)
