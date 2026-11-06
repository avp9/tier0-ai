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

## Inspecting and evaluating retrieval

```bash
# Show the top matches for a question
uv run python scripts/inspect_retrieval.py "how do I reset my password" -k 5
# Score the 44 evaluation cases (Recall@k, Hit@k, MRR) and the Knowledge Gap separation
uv run python scripts/inspect_retrieval.py --evaluate
```

Both use the OpenAI embedder by default and need an index built with the same model. Add `--embedder fake` to use an index built with `build_index.py --embedder fake`; fake results only exercise the tooling and say nothing about real quality.

## Asking a question

```bash
OPENAI_API_KEY=... uv run python scripts/ask.py "How many casual leave days do I get?"
```

Prints the reply and why the agent answered or declined. The agent answers only when two checks pass: the best retrieved passage scores at least `RETRIEVAL_SCORE_THRESHOLD`, and an LLM confirms the retrieved passages are enough to answer. Otherwise it says it has no reliable answer and offers to raise a support ticket. Answers always end with a `Sources:` line listing the titles of the passages used.

## Checking the Jira integration

```bash
# Read-only: list the tickets of one requester in this environment
JIRA_SERVER=https://you.atlassian.net JIRA_USERNAME=you@example.com JIRA_API_TOKEN=... \
APP_ENV=staging uv run python scripts/jira_smoke.py alice@buzztech.example
# Also create one clearly labelled test ticket and read it back
... uv run python scripts/jira_smoke.py alice@buzztech.example --create
```

Every ticket carries a `tier0-env:<APP_ENV>` label and lookups only return tickets with the signed-in requester's email and the current environment's label, so staging and production never see each other's tickets.
