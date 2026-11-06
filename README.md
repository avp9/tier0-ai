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

## Classifying what an employee wants

```bash
TYPESAFE_API_KEY=... OPENAI_API_KEY=... \
  uv run python scripts/classify_intent.py "any news on my ticket?"
```

Each message is classified into an intent (knowledge question, create ticket, ticket status, small talk, out of scope, ambiguous), a domain (IT, HR, other) and whether it continues the earlier topic. JEV (`typesafe_sdk`, 10 s timeout, 2 retries) is tried first; if it fails or returns something unusable, OpenAI is used. The result says which provider answered. If both fail the caller gets `IntentUnavailable` and should ask the employee to retry.

## Database and conversation state

Conversation state is stored in PostgreSQL through LangGraph's `PostgresSaver`, so a conversation survives restarts. Schema changes are Alembic migrations that ship inside the package:

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/tier0 \
  uv run python -c "from tier0.db.migrate import upgrade_to_head; import os; upgrade_to_head(os.environ['DATABASE_URL'])"
```

To run the database tests locally, point `TIER0_TEST_DATABASE_URL` at any Postgres you can create databases in (each test creates and drops its own database). Without it those tests are skipped; CI always runs them.

## Raising a ticket

The employee is never asked for their email (it comes from the login). The flow, in `tier0.agent.ticket_flow`:

1. **Collect:** the agent drafts a title, description and category from the conversation and asks only for what is missing, one question at a time. If the language model is down it falls back to quoting the employee's own words.
2. **Preview:** it shows the requester, title, category, priority and description and asks for confirmation.
3. **Confirm:** only a clear yes creates the ticket. `no` cancels. Anything else (a question, "yes but...", "maybe") is asked again, and after five unclear answers it stops without creating anything.
4. **Create once:** the ticket carries a submission id. Repeats, double clicks, retries after a timeout, and a crash right after Jira accepted the ticket all end with exactly one ticket, because the agent looks the ticket up by that id before ever creating another.

The conversation pauses at each question and can be resumed after a server restart.
