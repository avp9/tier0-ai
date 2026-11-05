# Tier0 AI — Product Backlog

Each item is one PR. Pick the highest-priority item whose dependencies are **Done**. Status values: `Todo` · `In progress` · `In review` · `Done`.

**Before T01:** the owner installs and logs in to `gh` and `az`, and creates the pay-as-you-go Azure subscription. This is only needed for M5, but setting it up early avoids blocking later.

## M0 — Foundations

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T01 | `chore/repo-bootstrap` | Create the public GitHub repo `tier0-ai` and restructure into `backend/`, `frontend/` and `data/` | Repo exists. Initial commit has only `README.md`, `.gitignore` and the planning docs (the one allowed direct push to an empty `main`). The PR moves the existing code into the target layout, adds `pyproject.toml` (uv, Python 3.12) and `.env.example`, configures ruff and mypy, and adds a pytest smoke test. No secrets committed. | – | Done |
| T02 | `chore/ci-pipeline` | PR CI and repo protections | `ci.yml` runs format, lint, type checks and tests on PRs, with least-privilege permissions. Branch protection on `main` (required checks, 1 approval, no force pushes). Secret scanning, push protection and Dependabot are on, and CodeQL is enabled. | T01 | Done |
| T03 | `chore/dataset-licensing` | Check source licences and prune `qa_dataset.json` | Each Hugging Face source's licence is recorded in `data/knowledge_base/SOURCES.md`. Entries from sources that don't allow redistribution are removed. `build_qa_dataset.py` moves to `tools/`, and its output is reproducible. | T01 | Done |
| T04 | `feat/knowledge-gaps` | Define Knowledge Gaps and remove matching content | `data/evaluation/knowledge_gaps.json` lists 10–15 topics, each with example questions. A script removes matching entries, and a test asserts that none remain (keyword check; the embedding-similarity check moves to T08, which has real embeddings). | T03 | In review |
| T05 | `feat/synthetic-kb-coverage` | Synthetic Onboarding and Payroll (plus BuzzTech HR policy) entries | At least 50 entries clearly labelled synthetic BuzzTech content, covering the spec's Onboarding and Payroll & Compensation topics and filling HR gaps (leave, WFH, reimbursements, code of conduct). None may cover a Knowledge Gap topic; extend the T04 no-gap-content test to the synthetic files. | T03 | Todo |
| T05b | `chore/kb-company-normalization` | Rewrite the borrowed "Kreeda Labs" HR entries for BuzzTech | Company name and contact addresses in the 644 `strova-ai` entries are replaced with BuzzTech Solutions equivalents (clearly fictional). A test asserts no "Kreeda" or `kreedalabs.com` text remains in the KB. Keeps MIT attribution in `SOURCES.md`. | T03 | Todo |
| T06 | `refactor/kb-schema` | Typed KB records and loader | Pydantic `KnowledgeRecord` (`document_id`, `title`, `category`, `source`, `content`, `last_updated`, `synthetic`). The loader validates every file and rejects duplicates and empty content, and assigns stable `document_id`s (the builder's `TICKET_<n>` numbers shift whenever sources change). Unit tests cover all of this. Fixture KB of about 60 entries in `data/fixtures/`. | T04, T05, T05b | Todo |

## M1 — Retrieval and grounded answers

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T07 | `feat/chroma-index-build` | Repeatable index build | `scripts/build_index.py` builds the Chroma index. A hash of the KB files and embedding model is written into the index metadata, and the build is skipped when the hash matches. Fake embeddings for tests. Integration test against the fixture KB. | T06 | Todo |
| T08 | `feat/retrieval-eval` | Retrieval inspection and Recall@k | `scripts/inspect_retrieval.py` prints the top-k results with scores. `data/evaluation/retrieval_cases.json` has 30–50 cases. Recall@k is computed (reusing the existing `calculate_metrics`). An embedding-similarity check confirms Knowledge Gap example questions retrieve nothing above the answerability threshold. | T07 | Todo |
| T09 | `feat/grounded-answers` | Answerability check and grounded answers with sources | Two signals: a score threshold (tuned on the evaluation cases) and an LLM sufficiency check. The decision is stored in the agent state. Answers cite document titles. Knowledge Gap questions produce a gap acknowledgement and a Ticket offer. Injection text inside a retrieved chunk is ignored (unit test with a fake LLM). | T08 | Todo |

## M2 — Tickets

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T10 | `refactor/jira-adapter` | Ticket adapter interface and hardened Jira adapter | `TicketAdapter` protocol, plus `JiraAdapter` and `FakeTicketAdapter` implementations. Create returns the key and URL. Errors raise typed exceptions (unavailable, rejected, unknown outcome). JQL values are quoted and escaped. Project key, custom field and env label come from config. Connects lazily. Unit tests on the request/response mapping. | T01 | Todo |
| T11 | `feat/ticket-status` | Status lookup with comments | Returns status, updated time and the latest support comments. Handles zero, one and many matches, and an API error is distinguished from an empty result. Filtered by Requester Email and env label. Covers AT-10, AT-11, AT-12 and AT-13 at the service level. | T10 | Todo |
| T12 | `feat/ticket-creation-flow` | Field collection, preview, confirmation and idempotent submit | Collects title and description (summarising the conversation), infers the category, and treats priority as optional. Shows a preview, then pauses at `interrupt()`: a clear yes submits, no cancels, and anything ambiguous re-asks. `ticket_submissions` row and `tier0-sub:<id>` label prevent duplicates. Covers AT-05 to AT-08 and AT-18. | T10, T14a | Todo |

## M3 — Agent

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T13 | `feat/intent-classification` | Intent types, JEV client and OpenAI fallback | `intent.py` defines the intent enum and result types. JEV is called through `typesafe_sdk` with a timeout and 2 retries on transient errors, then falls back to OpenAI structured output. Malformed output is validated and recovered from. Unit tests use fakes for both providers. Covers AT-14. | T01 | Todo |
| T14a | `feat/agent-state-postgres` | Typed state and `PostgresSaver` | Typed `AgentState` with explicit workflow status values. `PostgresSaver` is wired up, with Alembic migrations for `users` and `ticket_submissions`. Integration test against a Postgres service container in CI. | T02 | Todo |
| T14b | `feat/agent-graph` | Connect all flows | The graph routes knowledge, create-ticket, status, small talk, out-of-scope and ambiguous intents. Topic context is kept across turns. Multi-turn integration tests cover all three primary flows with fakes, plus AT-15, AT-16 and AT-17. | T09, T11, T12, T13, T14a | Todo |

## M4 — Application

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T15 | `feat/api-auth` | FastAPI app, demo users and sessions | Demo users are created from the `DEMO_USERS` secret with argon2 hashes. Login, logout and me endpoints. Signed HttpOnly session cookie. Login is rate-limited. `/api/health` and `/api/ready` endpoints. | T14a | Todo |
| T16 | `feat/chat-api` | `/api/chat` endpoints | Contracts match PROJECT_PLAN §5. The Requester Email always comes from the session. Threads are scoped to their owner, and other users' threads return 404. API tests with fakes. | T14b, T15 | Todo |
| T17 | `feat/react-chat-ui` | React login and chat UI | Login page and chat view with sources, a Ticket preview card with Confirm/Cancel, and a ticket selection list. Accessible (labels, keyboard, contrast). FastAPI serves the built app. Vitest component tests. | T16 | Todo |
| T18 | `test/playwright-e2e` | Docker Compose stack and Playwright in CI | `docker compose up` starts the app and Postgres with fake providers and the fixture index. Playwright covers login and AT-01 to AT-18 (with AT-04 and AT-09 reinterpreted per ADR 0001). Added as a required CI check. | T17 | Todo |

## M5 — Azure delivery

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T19 | `chore/dockerfile` | Multi-stage production image | Stages: frontend build, index build (needs the real index as input), then a slim Python 3.12 runtime running as a non-root user. Image size and cold start are measured and recorded. | T18 | Todo |
| T20 | `chore/azure-infra` | Bicep infrastructure and OIDC setup | `infra/main.bicep` defines the resource group contents (Container Apps environment, staging and prod apps, Postgres B1ms with two databases, Log Analytics). OIDC federated credentials with roles scoped to the resource group, plus a guided walkthrough for the owner's one-time manual steps. Cost estimate checked against the Azure pricing calculator. **Needs owner approval (infrastructure cost).** | T19 | Todo |
| T21 | `chore/deploy-pipeline` | Staging and gated production deploys | `deploy.yml`: cached index build, image pushed to GHCR (tagged by SHA), staging deploy, smoke tests, then a `production` environment approval before deploying the same SHA. Secrets go from GitHub Environments to Container Apps secrets. A rollback procedure is documented. | T20 | Todo |

## M6 — Quality and submission

| ID | Branch | Item | Acceptance criteria | Depends on | Status |
|---|---|---|---|---|---|
| T22 | `feat/eval-workflow` | Nightly and on-demand evaluation | `eval.yml` runs `run_eval.py` against the real providers (staging label) and measures Recall@k, groundedness, routing accuracy, escalation rate, ticket flows, p50/p95 latency and cost. Report saved as an artifact and in `docs/evaluation-report.md`. | T21 | Todo |
| T23 | `feat/observability` | Structured logs and correlation IDs | JSON logs carry a correlation ID per request and per thread, with emails masked and no secrets or message bodies at INFO. Logs flow to Log Analytics. | T16 | Todo |
| T24 | `test/failure-and-adversarial` | Harden the failure paths | Tests for OpenAI and JEV outages, Jira timeouts with an unknown outcome, rejected payloads, and prompt injection in user input and in KB content. Issues found are fixed. | T14b | Todo |
| T25 | `docs/submission` | README, architecture, design decisions, demo | README with a clean-clone setup, environment variables, KB build steps, run and deploy instructions. Architecture diagram. `design-decisions.md` (spec deviations, ADR links). Demo script, plus screenshots or a recording of all three flows. CHANGELOG. | T22 | Todo |

## Stretch (after all baseline items are done)

| ID | Item |
|---|---|
| S1 | Hybrid retrieval (BM25 + dense + re-ranker), with a measured Recall@k improvement |
| S2 | Microsoft Entra ID sign-in replacing demo accounts (only the identity source changes) |
| S3 | Specialised IT and HR sub-agents |
| S4 | Semantic or LLM response cache |
| S5 | Cross-session memory |

## Dependency order (critical path)

```text
T01 → T02 → T14a ─────────────────────┐
T01 → T03 → T04/T05 → T06 → T07 → T08 → T09 ─┤
T01 → T10 → T11 ──────────────────────┤
          T10 + T14a → T12 ───────────┤
T01 → T13 ────────────────────────────┴→ T14b → T16 → T17 → T18 → T19 → T20 → T21 → T22 → T25
                                    T14a → T15 ┘
```

**First recommended item: T01**, once the plan is approved.
