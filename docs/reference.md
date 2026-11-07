# Component reference

Commands and behaviour for each part of Tier0, collected as the project was built. For setup start with the [README](../README.md); for how the parts fit together see [architecture.md](architecture.md).

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

## The agent

`tier0.agent.service.AgentService.send(thread_id=..., requester_email=..., message=...)` handles one employee message and returns a `ChatTurn`: the reply, what the agent is now waiting for (`offer_ticket`, `collect_ticket_details`, `confirm_ticket`, `select_ticket`, or nothing), sources, a created ticket if any, and the workflow status. It resumes a paused conversation automatically, including after a restart, and refuses a thread that belongs to a different employee.

Each message is classified, then routed:

| Intent | What happens |
|---|---|
| Knowledge question | Answer with sources, or (no reliable answer) offer a ticket. Follow-ups are rewritten as standalone questions first. |
| Create ticket | Collect details, preview, explicit yes, create once. A low-confidence request is checked first. |
| Ticket status | None / one / several (pick one). A typed ticket key is looked up directly, scoped to the requester. |
| Small talk, out of scope, ambiguous | Fixed polite replies and a clarifying question; no model call, no action. |

If the employee ignores an offer or a list and says something new, the new message is handled as a fresh request.

## Running the web API

```bash
cd backend
export DATABASE_URL=postgresql://user:password@localhost:5432/tier0
export SESSION_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export DEMO_USERS='[{"email":"alice@buzztech.example","name":"Alice Demo","password":"a-demo-password-1"}]'
uv run uvicorn --factory tier0.api.app:create_app_from_env --port 8000
```

On start the app migrates the database (safe if several servers start together) and creates any demo user that doesn't exist yet; it never changes an existing account, so changing a password in `DEMO_USERS` does not alter an existing user. The one exception is `gender` (optional, `"male"` or `"female"`, which picks the header icon): an account that has none gets it filled in, and one that is already set is left alone.

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | The process is up (liveness). |
| `GET /api/ready` | Database reachable and schema at this build's version, else `503`. |
| `POST /api/auth/login` | `{email, password}` -> `204` and a session cookie, `401` (same message for a wrong password or an unknown account), or `429` after repeated failures. |
| `POST /api/auth/logout` | Clears the cookie. |
| `GET /api/auth/me` | The signed-in employee, or `401`. |

Sessions are signed, `HttpOnly`, `SameSite=Lax` cookies (also `Secure` unless `APP_ENV=local`) that expire after `SESSION_MAX_AGE_HOURS`. Logout clears the browser's cookie, but a copied cookie stays valid until it expires or the user is removed, which is why sessions are short. API docs at `/docs` are served only when `APP_ENV=local`.

## Chat API

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | `{thread_id?, message}` -> reply, intent, sources, `pending_action` (what the agent is waiting for; a ticket preview also says whether it is `editable`), and `ticket` once one is created. Omit `thread_id` to start a conversation. To change a ticket preview before it is sent, send `{thread_id, ticket_edit: {title, description, category, priority}}` instead of a `message` (all four fields; `priority` may be `null`): the preview is replaced and nothing is sent. `409` if no preview is showing, or if a send was already attempted (the ticket may exist, so it can no longer be edited). |
| `GET /api/chat/{thread_id}` | The conversation so far and any open question, for a thread you own (otherwise `404`). |
| `GET /api/diagnostics/retrieval` | The search-quality check shown by the header's heart-beat button: the output of `scripts/inspect_retrieval.py --evaluate` as data (hit rate and recall at 1, 3 and 5, MRR, by kind of question, the questions it missed, how well scores separate answerable from decline-worthy questions, and a suggested `RETRIEVAL_SCORE_THRESHOLD`). Any signed-in employee may read it. A run calls the embedding service once per test question (about half a minute), so a result under 10 minutes old is returned as it is, and `?refresh=true` is refused with `429` and `Retry-After` for 30 seconds after a run. `404` if the evaluation questions are not in this deployment, `503` if chat is not running. |
| `GET /api/conversations` | The signed-in employee's chats, newest first, as `[{thread_id, title, updated_at}]` for the history sidebar (up to 50). The title is the first message, cut to 80 characters. Only your own chats are listed. |

The employee is always the signed-in one; nothing in the request body can change who a ticket is for. Buttons in the UI simply send `yes`, `no` or a ticket key as ordinary messages. Messages are limited to 2000 characters and 20 per minute per employee.

Chat needs `OPENAI_API_KEY`, `JIRA_SERVER`, `JIRA_USERNAME`, `JIRA_API_TOKEN` and a built knowledge index (see "Building the knowledge-base index"). Without them a local server still starts (chat answers `503`), but a server with `APP_ENV` other than `local` exits at start-up and names what is missing. `/api/ready` also checks that the index is readable.

## Web app (frontend)

React + TypeScript + Vite in `frontend/`. Needs Node 22 or newer.

```bash
cd frontend
npm ci
npm run dev        # http://localhost:5173, forwards /api to the API on port 8000
npm test           # component tests (Vitest)
npm run lint && npm run typecheck
npm run build      # produces frontend/dist
```

To serve the built app from the API (one origin, one process), set `STATIC_DIR=../frontend/dist` before starting the API. The server then returns the app for every non-API path, with a strict Content-Security-Policy (no inline script or style), long-lived caching for the hashed assets and `no-cache` for the page itself. A server that is not running locally refuses to start if `STATIC_DIR` has no build in it.

What the UI does: sign in; ask a question and see the answer with its sources as a list; accept or decline a ticket offer with buttons; review a ticket card with **Create ticket** / **Cancel** (the ticket is only created by the employee's own click); pick one of several tickets from a list; reload the page and carry on (the conversation id lives in this tab's session storage; nothing else is stored). Replies are always shown as plain text, so markup in a reply or a source title is displayed, never run, and a ticket link is only shown if its address is http(s).

## End-to-end tests (Docker Compose + Playwright)

`docker compose up -d --build --wait` starts the real app and Postgres with **fake** language models and a **fake** ticket system, serving the built UI on <http://localhost:8000>. Fakes are refused unless `APP_ENV` is `local` or `e2e`, and the test-control endpoints (`/api/_e2e/*`, used to seed tickets and script failures) exist only when `APP_ENV=e2e` and `E2E_TOKEN` is set; without the right `X-E2E-Token` they answer 404.

```bash
docker compose up -d --build --wait
cd e2e && npm ci && npx playwright install chromium
E2E_TOKEN=e2e-local-token npm test      # login, AT-01 to AT-18, accessibility (axe)
docker compose down -v
```

AT-04 and AT-09 are reinterpreted per ADR 0001: the requester's email always comes from the sign-in session, so the agent never asks for one. The fake "models" are deterministic rule-based stand-ins; they prove the wiring, not answer quality (that is the evaluation workflow, T22).

## Production image

`docker build --target prod --build-context index=<dir> --build-arg INDEX_HASH=<hash> -t tier0 .` builds the runtime image (Python 3.12 slim, non-root user `tier0`, UI and prebuilt knowledge-base index baked in). The index is built outside the image (`backend/scripts/build_index.py`, which needs `OPENAI_API_KEY`) so that no key ever enters an image layer; building without an index or its `INDEX_HASH` fails on purpose, even when layers are cached (the hash is also stored as the image label `tier0.index-hash`). All settings come from environment variables at run time (see `.env.example`); outside `local`/`e2e` the server refuses to start if any are missing.

Measured on an arm64 laptop with the full 20,935-record knowledge base (indexed with the fake embedder, so the index is a stand-in for the real one, which will be larger):

| Measure | Result |
|---|---|
| Image size | 1.21 GB (venv 598 MB, index 145 MB) |
| Cold start to a healthy `/api/health` | 1.5 to 2.3 s (3 runs, Postgres already running) |
| Memory after start | about 190 MB |

The largest dependency is the `kubernetes` client (119 MB), pulled in by `chromadb` and unused here; trimming it is a possible later optimisation, left alone because removing files from a dependency risks breaking it.

## Evaluation

`backend/scripts/run_eval.py --providers real|fake` measures retrieval, answers, routing, ticket flows, latency and estimated cost, using the cases in `data/evaluation/`. The **Evaluation** workflow runs it nightly with the real providers (and skips with a warning until the keys are set). See `docs/evaluation-report.md` for what each number means and its limits. No real-provider results have been recorded yet.

## Logs

The server writes one JSON line per event with a request ID (also returned in the `X-Request-ID` header) and a conversation ID, with email addresses masked and no message text. `LOG_FORMAT` (`auto`/`json`/`text`) and `LOG_LEVEL` control it. See `docs/observability.md` for the fields, what is never logged, and how to follow a request in Log Analytics.

## Failure and adversarial testing

`docs/failure-and-adversarial-testing.md` lists what was attacked or broken on purpose, the problems that found (including a login lock-out that would have affected every user behind the Azure ingress), and what is still open. New settings: `TRUSTED_PROXY_HOPS` (reverse proxies in front of the app; 0 locally, 1 on Azure) and `MAX_REQUEST_BYTES` (default 65,536).
