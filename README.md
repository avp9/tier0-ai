# Tier0 AI

An intelligent IT & HR operations agent for the fictional company BuzzTech Solutions. It answers employee questions from a curated knowledge base, offers to raise a support ticket when it cannot answer, and reports the status of existing tickets.

Status: under development. See [PROJECT_PLAN.md](PROJECT_PLAN.md) for the architecture and [PRODUCT_BACKLOG.md](PRODUCT_BACKLOG.md) for progress. Setup and deployment instructions will be added as features land.

## Building the knowledge-base index

From `backend/` (Python 3.12, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
# Local test index with no API key (bag-of-words fake embeddings):
uv run python scripts/build_index.py --embedder fake
# Real index (needs OPENAI_API_KEY; model from EMBEDDING_MODEL):
OPENAI_API_KEY=... uv run python scripts/build_index.py
# Content hash only, used as the CI cache key:
uv run python scripts/build_index.py --print-hash
```

The index is written to `.data/chroma`. A second run with unchanged data and the same embedding model is skipped.
