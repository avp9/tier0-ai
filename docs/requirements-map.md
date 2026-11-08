# Requirements map

Where each requirement of the specification (`SmartDesk_AI_Complete_Specification.md`) is met, what proves it, and what is deliberately different. Paths are from the repository root. "E2E" means the Playwright suite in `e2e/tests` (run in CI on every pull request against the whole stack with fake providers); "unit" and "integration" are `backend/tests`.

## Functional requirements

| ID | Requirement | Implemented in | Proof |
|---|---|---|---|
| FR-RAG-01 | 30–50+ entries, IT and HR, onboarding, payroll | `data/knowledge_base/` (20,935 records: 465 HR policy Q&A, 20,414 IT ticket resolutions, 56 synthetic BuzzTech handbook, new-hire, payroll and equity articles; licences in `SOURCES.md`) | `test_full_knowledge_base.py`, `test_synthetic_kb.py`, `test_kb_loader.py` |
| FR-RAG-02 | Structured data with metadata | `rag/schema.py` (`document_id`, `title`, `category`, `source`, `content`, `last_updated`, `synthetic`) | `test_kb_loader.py` |
| FR-RAG-03 | Repeatable ingestion and indexing; dense, sparse or hybrid | `scripts/build_index.py`, `rag/indexer.py` (Chroma dense index, content-hashed, atomic swap), `rag/sparse.py` (SPLADE keyword index), `rag/rerank.py` | `test_index_build.py`, `test_hybrid_search.py`; no splitting, and why: [chunking strategy](design-decisions.md#chunking-strategy) |
| FR-RAG-04 | Retrieval relevant and inspectable | `scripts/inspect_retrieval.py`, `scripts/compare_search.py`, 44 labelled questions in `data/evaluation/retrieval_cases.json`, Recall@k / Hit@k / MRR in `rag/evaluation.py`, the header's live search check (`rag/diagnostics.py`) | [evaluation report](evaluation-report.md), [hybrid search](hybrid-search.md) |
| FR-RAG-05 | Grounded answers, no invention, sources, injection-safe | `rag/answering.py` (answer only from passages; sources looked up from retrieval, never from the model), `rag/answerability.py` (passages fenced and escaped) | `test_answerability.py`, `test_adversarial.py`; E2E AT-01, AT-02, AT-16 |
| FR-RAG-06 | Confidence and escalation | `rag/answerability.py`: score threshold (0.30 cosine, or 0.12 on the reranker score) **and** a model check that the passages answer the question; the decision is stored in state | `test_answerability.py`, `test_threshold_suggestion.py`; threshold advice in the header search check |
| FR-RAG-07 | Deliberate knowledge gaps | `data/evaluation/knowledge_gaps.json` (14 topics, 42 questions); matching entries removed from the knowledge base (`scripts/prune_knowledge_gaps.py`) | `test_knowledge_gaps.py`; E2E AT-03; evaluation: knowledge-gap questions answered anyway |
| FR-ORCH-01 | Request classification | `intent/` (JEV primary, OpenAI structured-output fallback; knowledge question, create ticket, ticket status, greeting/small talk, ambiguous, out of scope) | `test_intent.py`; routing accuracy in the evaluation report |
| FR-ORCH-02 | One agent loop | `agent/graph.py` (LangGraph), `agent/service.py` | `test_agent_graph.py` |
| FR-ORCH-03 | Multi-turn context | LangGraph state checkpointed in PostgreSQL (`agent/checkpoint.py`, `agent/state.py`) | `test_checkpointing.py`; E2E AT-15 |
| FR-ORCH-04 | Ambiguity handling | `agent/graph.py` clarification path; `tickets/confirmation.py` | `test_confirmation.py`; E2E AT-08 |
| FR-ORCH-05 | Out of scope, small talk | `agent/replies.py`, `agent/graph.py` | E2E AT-17 |
| FR-TKT-01 | External ticketing | `tickets/jira_adapter.py` behind `tickets/base.py`; **run against a live Jira Cloud project** (SUP), see [evaluation report](evaluation-report.md#live-jira). A fake adapter (`tickets/fake.py`) is used by tests, the demo stack and the evaluation | `test_jira_adapter.py`, `test_tickets_models_jql.py` |
| FR-TKT-02 | Mandatory fields before creation | `tickets/drafting.py`, `agent/ticket_flow.py` (summary, description, category, priority; requester from the session) | `test_ticket_drafting.py`; E2E AT-05 |
| FR-TKT-03 | Preview and explicit confirmation | `tickets/confirmation.py` (a deterministic parser decides "yes"; a model cannot be talked into it), `agent/ticket_flow.py`; the preview can also be edited (E2E AT-19) | `test_confirmation.py`, `test_ticket_flow.py`; E2E AT-06, AT-07, AT-08 |
| FR-TKT-04 | ID or link after success; no false success | `tickets/submission.py`, `agent/replies.py` | `test_submission_service.py`; E2E AT-06, AT-13 |
| FR-TKT-05 | Useful description | `tickets/drafting.py` (drafted from the conversation, facts kept apart from assumptions) | `test_ticket_drafting.py` |
| FR-TKT-06 | Duplicate protection | `tickets/submission.py`, `tickets/postgres_store.py` (`ticket_submissions` table, partial unique index, a submission-id label on the ticket, "look before creating") | `test_submission_store.py`, `test_failure_paths.py`; E2E AT-18 |
| FR-STATUS-01 | Identify the employee | Employees sign in; the session email is the requester ([ADR 0001](adr/0001-signed-in-email-is-requester-email.md)) | E2E AT-04, AT-09 (see differences below) |
| FR-STATUS-02 | Look up through the ticket service | JQL on the requester field and environment label, no local mapping needed (`tickets/jql.py`, `tickets/status.py`) | `test_tickets_models_jql.py`, `test_ticket_status.py` |
| FR-STATUS-03 | Status and support updates | `tickets/status.py` (latest public comment; internal comments never shown) | E2E AT-10 |
| FR-STATUS-04 | Several tickets | Listed as choices; the list stays open so several can be inspected in turn | E2E AT-11 |
| FR-STATUS-05 | No tickets | Clear no-results reply | E2E AT-12 |
| FR-STATUS-06 | Access control | Only the signed-in employee's tickets; a ticket id typed by someone else is not looked up | `test_ticket_status.py`, `test_adversarial.py` |

## Non-functional and error handling

| Requirement | Implemented in | Proof |
|---|---|---|
| Secrets (8.2) | Environment variables only; `.env.example` has placeholders; `.env` is git-ignored; `scripts/package.sh` refuses to zip anything that looks like a key; logs mask emails and never hold message text | `test_observability.py`, `test_api_logging.py` |
| Error handling (9) | Retries with timeouts for the model and Jira; honest outage messages; unknown outcome after a timeout is reconciled, not retried blindly; malformed model output validated | `test_failure_paths.py`, `test_openai_chat.py`; E2E AT-13, AT-14, AT-18; [failure and adversarial testing](failure-and-adversarial-testing.md) |
| Security (10) | Retrieved text and user input treated as untrusted; authorisation in code, not in the model; body-size limit; login throttling | `test_adversarial.py`, `test_api_hardening.py`; E2E AT-16 |
| Code quality, README, diagram (12.4) | Modular packages (`rag/`, `tickets/`, `agent/`, `intent/`, `api/`); strict mypy, ruff; [README](../README.md); diagrams in [architecture](architecture.md) | CI: `backend`, `frontend`, `e2e`, `image`, `infra` |
| Observability | Structured JSON logs with request and conversation ids; optional LangSmith tracing with personal details masked | [observability](observability.md), [LangSmith](langsmith.md) |

## Acceptance tests

| Test | Where it runs | Note |
|---|---|---|
| AT-01 to AT-18 | `e2e/tests/*.spec.ts` (each test name starts with its id), plus unit and integration tests for the same behaviour | AT-19 (editing the preview) is an addition |
| AT-04, AT-09 | `e2e/tests/tickets.spec.ts`, `status.spec.ts` | **Reinterpreted:** the spec expects the agent to ask for the employee's email. Here employees sign in, so the agent never asks and uses the signed-in email; the tests assert exactly that. Reason: an email typed into a public chat would let anyone read someone else's tickets (the risk FR-STATUS-06 describes). See [design decisions](design-decisions.md) |

## Rubric (100 marks)

| Category | Where to look |
|---|---|
| A. Knowledge base and RAG (25) | Breadth: `data/knowledge_base/`. Chunking strategy and justification: [design decisions](design-decisions.md#chunking-strategy). Retrieval quality: [evaluation report](evaluation-report.md), [hybrid search](hybrid-search.md). Grounding: `rag/answering.py`, groundedness proxy and knowledge-gap results in the evaluation report. Escalation: `rag/answerability.py` |
| B. Ticket creation (20) | `tickets/`, `agent/ticket_flow.py`; live Jira evidence in the evaluation report; E2E AT-05 to AT-08 |
| C. Ticket status (15) | `tickets/status.py`; E2E AT-09 to AT-13 |
| D. Orchestration and routing (15) | `agent/graph.py`, `intent/`; routing accuracy in the evaluation report; E2E AT-15 to AT-17 |
| E. Code quality and documentation (15) | This repository; [README](../README.md); [architecture](architecture.md) (Mermaid diagrams) |
| F. Error handling and robustness (10) | [failure and adversarial testing](failure-and-adversarial-testing.md); E2E AT-13, AT-14, AT-18 |

## Bonus

| Bonus | Status |
|---|---|
| Hybrid search (dense + sparse + re-ranker) with demonstrable improvement | **Done.** SPLADE + cross-encoder reranker; hit@1 0.68 → 0.84, hit@5 0.89 → 1.00; the agent answers 100% instead of 82% with a correct citation ([hybrid search](hybrid-search.md)) |
| Multi-agent (IT and HR sub-agents) | Not built; one agent covers both (see [design decisions](design-decisions.md)) |
| Evaluation pipeline with quantitative metrics | **Done.** `scripts/run_eval.py`, a nightly workflow, LangSmith datasets and experiments, a 191-question reference-answer set with a judge model ([evaluation report](evaluation-report.md), [LangSmith](langsmith.md)) |
| User-facing deployment | **Done.** React web app served by the API; one-command run on a Mac or an Azure VM ([RUN.md](../RUN.md)) |
| Caching | **Done.** Redis semantic cache ([answer cache](semantic-cache.md)) |
| Cross-session memory | Not built; conversations persist and can be resumed, but nothing is remembered across new conversations |

## Submission checklist (section 16)

| Item | Where |
|---|---|
| Repository with source, knowledge base, configuration | This repository |
| README with setup, environment variables, how to populate the knowledge base, how to run, approach | [README](../README.md) ("Run it", "Configuration", "Run it with real providers") |
| Architecture diagram | [architecture](architecture.md) |
| Demo (recording or screenshots) of RAG answer, ticket creation, ticket status | [demo script](demo-script.md), `docs/demo/` (screenshots and a recording of the ticket flow) |
| Knowledge-base files | `data/knowledge_base/` |
| Evaluation results and known limitations | [evaluation report](evaluation-report.md); limits in [design decisions](design-decisions.md) |
