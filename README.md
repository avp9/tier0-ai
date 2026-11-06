# Tier0 AI

An IT and HR help-desk agent for the fictional company BuzzTech Solutions. An employee signs in and chats. Tier0 answers from a curated knowledge base (with sources), offers to raise a support ticket when it cannot answer, creates the ticket only after an explicit confirmation, and reports the status of the employee's own tickets.

![A ticket preview waiting for confirmation](docs/demo/04-ticket-preview.png)

## What is and is not proven

| | Status |
|---|---|
| Backend, agent, API, web app | Built and tested: 798 backend tests, 111 frontend tests, 28 browser tests (AT-01 to AT-18, accessibility), all run in CI on every pull request |
| Real OpenAI, JEV and Jira | **Never exercised with real credentials.** Everything that touches them is tested against fakes and recorded responses. Run `backend/scripts/jira_smoke.py` and the Evaluation workflow once keys exist |
| Answer quality numbers | **None yet.** The evaluation harness exists and runs; no run with real models has been recorded (see [evaluation report](docs/evaluation-report.md)) |
| Azure deployment | Infrastructure code, pipelines and docs are written and linted. **Nothing has been deployed**; the pipelines have never run ([Azure setup](docs/azure-setup.md)) |

The screenshots and recording in [docs/demo](docs/demo/) come from the fake-provider stack: the "answers" are a stand-in model quoting the knowledge base, so they show the product's behaviour, not real answer quality.

## Try it in two minutes (no API keys)

You need Docker. This starts the real app and PostgreSQL with fake language models and a fake ticket system, serving the web app on <http://localhost:8000>:

```bash
docker compose up -d --build --wait
```

Sign in with `alice@buzztech.example` / `alice-demo-pass-1` (these are throwaway demo values for this local stack only). Try:

- `Unable to reset password via self-service portal`: a knowledge answer with its source
- `My monitor keeps flickering, how do I fix it?`: not in the knowledge base, so it offers a ticket
- `What is the status of my tickets?`

Stop it with `docker compose down -v`. The [demo script](docs/demo-script.md) walks through all three flows.

## Run it from source

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and a PostgreSQL 16 you can reach.

```bash
# 1. backend dependencies and a small test index (fake embeddings, no key)
cd backend && uv sync
uv run python scripts/build_index.py --embedder fake --kb-dir ../data/fixtures --index-dir ../.data/chroma-fixture

# 2. the web app
cd ../frontend && npm ci && npm run build && cd ../backend

# 3. run with fake providers (APP_ENV=local allows them)
export DATABASE_URL=postgresql://user:pass@localhost:5432/tier0
export SESSION_SECRET=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export DEMO_USERS='[{"email":"alice@buzztech.example","name":"Alice Demo","password":"a-demo-password-1"}]'
export APP_ENV=local LLM_PROVIDER=fake TICKETING_PROVIDER=fake
export VECTOR_STORE_PATH=../.data/chroma-fixture STATIC_DIR=../frontend/dist
uv run uvicorn --factory tier0.api.app:create_app_from_env --port 8000
```

The app creates its tables on start and creates the demo users that do not exist yet.

## Run it with real providers

1. **Build the real index** (about 21,000 entries; a few minutes and a small OpenAI charge): `OPENAI_API_KEY=... uv run python scripts/build_index.py` writes `.data/chroma`. A second run with unchanged data and the same embedding model is skipped.
2. **Tune the answer threshold** with `uv run python scripts/inspect_retrieval.py --evaluate`. The default `RETRIEVAL_SCORE_THRESHOLD=0.30` is provisional, picked without real embeddings.
3. **Check Jira** with `uv run python scripts/jira_smoke.py` (needs the `JIRA_*` settings below and a project with a "Requester Email" field).
4. **Run** as above with `LLM_PROVIDER=openai TICKETING_PROVIDER=jira` (the defaults) and the keys set.

Outside `APP_ENV=local` or `e2e` the server **refuses to start** if a required setting is missing, and refuses fake providers.

## Configuration

All settings are environment variables ([`.env.example`](.env.example) has a template; never commit real values).

| Variable | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL connection string |
| `SESSION_SECRET` | yes | At least 32 random characters; signs session cookies |
| `DEMO_USERS` | yes, to sign in | JSON list of `{email, name, password}` (password at least 12 characters), created at start-up and stored hashed |
| `APP_ENV` | | `local` (default), `e2e`, `staging` or `production`. Anything else than local/e2e uses secure cookies, hides API docs and fails fast on missing settings. Also becomes the Jira label `tier0-env:<APP_ENV>` |
| `OPENAI_API_KEY`, `OPENAI_MODEL_NAME`, `EMBEDDING_MODEL` | with `LLM_PROVIDER=openai` | Answers and embeddings (defaults `gpt-4o-mini`, `text-embedding-3-small`) |
| `TYPESAFE_API_KEY` | optional | Enables JEV intent classification; OpenAI is the fallback |
| `JIRA_SERVER`, `JIRA_USERNAME`, `JIRA_API_TOKEN` | with `TICKETING_PROVIDER=jira` | Jira Cloud site and a dedicated bot user |
| `JIRA_PROJECT_KEY` (`SUP`), `JIRA_REQUESTER_FIELD_ID` / `_NAME`, `JIRA_ISSUE_TYPE`, `JIRA_SET_PRIORITY_FIELD` | | Where tickets go and how the requester is stored |
| `VECTOR_STORE_PATH` | | The Chroma index folder (default `.data/chroma`) |
| `RETRIEVAL_TOP_K`, `RETRIEVAL_SCORE_THRESHOLD` | | Retrieval depth and the minimum similarity to answer |
| `STATIC_DIR` | | The built web app (`frontend/dist`); the server serves it when set |
| `LLM_PROVIDER`, `TICKETING_PROVIDER` | | `openai`/`jira` (default) or `fake` (local and e2e only) |
| `SESSION_MAX_AGE_HOURS`, `LOGIN_MAX_FAILURES_PER_EMAIL`, `LOGIN_MAX_FAILURES_PER_IP`, `LOGIN_WINDOW_MINUTES`, `CHAT_MESSAGES_PER_MINUTE` | | Session length and rate limits |
| `TRUSTED_PROXY_HOPS`, `MAX_REQUEST_BYTES` | | Reverse proxies in front of the app (1 on Azure) and the request size cap |
| `LOG_LEVEL`, `LOG_FORMAT` | | See [observability](docs/observability.md) |
| `RUN_MIGRATIONS_ON_START` | | `true` by default |
| `E2E_TOKEN` | | Only with `APP_ENV=e2e`: enables the test-control endpoints |

## Tests and checks

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
TIER0_TEST_DATABASE_URL=postgresql://... uv run pytest     # database tests are skipped without it
cd frontend && npm run lint && npm run typecheck && npm test
docker compose up -d --build --wait && cd e2e && npm ci && npx playwright install chromium && E2E_TOKEN=e2e-local-token npm test
```

Pull requests must pass: backend, frontend, e2e, image, infra, dependency-audit and CodeQL. See [failure and adversarial testing](docs/failure-and-adversarial-testing.md) for what was attacked on purpose.

## Deploying

Target: Azure Container Apps (staging and production) plus PostgreSQL B1ms, about $16 to 21 a month. Nothing is deployed yet. The path is: [Azure setup](docs/azure-setup.md) (one-time, needs your approval for cost), then merges to `main` build one image per commit and deploy it to staging, run smoke tests, wait for a human to approve production, and deploy the same image ([rollback](docs/rollback.md)).

## Documentation

| | |
|---|---|
| [Architecture](docs/architecture.md) | Diagrams of the system, the conversation graph, the ticket flow and the delivery pipeline |
| [Design decisions](docs/design-decisions.md) | Where and why this departs from the specification, with the ADRs |
| [Demo script](docs/demo-script.md) | A 10-minute walkthrough of the three flows, with screenshots |
| [Component reference](docs/reference.md) | Commands and behaviour for each part |
| [Evaluation report](docs/evaluation-report.md), [Observability](docs/observability.md), [Failure testing](docs/failure-and-adversarial-testing.md), [Azure setup](docs/azure-setup.md), [Rollback](docs/rollback.md) | |
| [Project plan](PROJECT_PLAN.md), [backlog](PRODUCT_BACKLOG.md), [glossary](GLOSSARY.md), [changelog](CHANGELOG.md) | |

## Repository layout

```text
backend/    Python service: src/tier0 (api, agent, rag, tickets, intent, auth, observability), scripts, tests
frontend/   React + TypeScript + Vite web app
e2e/        Playwright browser tests and the demo capture
data/       knowledge base, evaluation cases, test fixtures (sources and licences in data/knowledge_base/SOURCES.md)
infra/      Bicep for Azure and the one-time setup script
deploy/     smoke tests run after each deployment
docs/       documentation and ADRs
```

Knowledge-base content comes from public datasets plus synthetic BuzzTech entries; see `data/knowledge_base/SOURCES.md` for sources and licences. This repository does not yet include a licence file for the code itself.
