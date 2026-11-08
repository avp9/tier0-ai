# Changelog

All notable changes, newest first. The project follows one small pull request per backlog item ([PRODUCT_BACKLOG.md](PRODUCT_BACKLOG.md)); the number in brackets is the pull request. No release has been tagged or deployed yet.

## Unreleased (first complete version, 2026-11-05 to 2026-11-06)

### After the first version
- **Submission readiness**: measured real-model evaluation recorded ([evaluation report](docs/evaluation-report.md)); routing and knowledge-gap escalation fixed after it found workplace questions and problem statements mishandled (routing 92% → 100%, gaps escalated 83% → 100%); chunking strategy documented; requirements map for graders ([docs/requirements-map.md](docs/requirements-map.md)); stale claims in the README and design decisions corrected; `backend/legacy/` removed.
- **Run scripts**: `scripts/run-local.sh` starts the whole app (app, PostgreSQL, Redis) in Docker on a Mac or Linux machine; `scripts/deploy-azure.sh` does the same on an Azure VM it creates (firewalled to your address); `scripts/package.sh` makes the submission zip with the prebuilt search index and a key scan. See [RUN.md](RUN.md).
- **Question-and-answer evaluation set** (`data/evaluation/qa_dataset.jsonl` / `.csv`, 191 examples with reference answers, key facts and correct sources, plus the unanswerable questions) generated from the knowledge base by `scripts/generate_eval_dataset.py`, uploaded as `tier0-qa` by `scripts/langsmith_eval.py`, and scored by a judge model for answer correctness. See [docs/langsmith.md](docs/langsmith.md).
- **Hybrid search** (`SEARCH_MODE=hybrid_rerank`): SPLADE keyword search next to the embedding search, merged by rank fusion, then re-read by a small cross-encoder. On the 44 evaluation questions hit@1 went from 0.64 to 0.84 and hit@5 from 0.82 to 1.00. Runs on CPU through ONNX Runtime (the same on a Mac and in Azure); models are baked into the image; falls back to embeddings alone if anything is missing. The container is now 1 vCPU / 2 GiB. See [docs/hybrid-search.md](docs/hybrid-search.md).
- **LangSmith**: every chat turn traced (search, model calls with tokens and cost, ticket calls) to `<agent-id>-<environment>`, with emails, long numbers and secrets masked first; thumbs up/down under each answer, stored in Postgres (`answer_feedback`) and attached to the trace; evaluation datasets and experiments (`scripts/langsmith_eval.py`). Never active in e2e. See [docs/langsmith.md](docs/langsmith.md).
- **Redis semantic cache** for answers, optional (`REDIS_URL`): the same question is answered from a key lookup with no embedding, a reworded one by embedding similarity, with no search and no model call. Never caches gaps, outages or questions with personal details; fails open; namespaced by index, models and prompts. See [docs/semantic-cache.md](docs/semantic-cache.md).

### Quality and delivery
- **T25** README with clean-clone setup, architecture, design decisions, demo script, screenshots and recording, this changelog.
- **T24** Failure and adversarial tests (#39). Fixed: login throttling that would have locked out every user behind Azure's ingress (new `TRUSTED_PROXY_HOPS`), no request size limit (new `MAX_REQUEST_BYTES`), blank model answers shown as answers, OpenAI replies with no choices crashing, ticket-write failures wrongly reported as "nothing happened", malformed Jira replies crashing, quotes in knowledge-base titles breaking the prompt fence.
- **T23** Structured JSON logs with request and conversation IDs; emails masked; no message text (#38).
- **T22** Evaluation harness, nightly workflow and report; no real-model run yet (#37).
- **T21** Staging deploy, smoke tests, approval-gated production deploy, rollback (#36).
- **T20** Bicep infrastructure, OIDC setup script, Azure walkthrough and cost estimate (#35).
- **T19** Multi-stage production image with the index baked in; size and cold start measured (#34).
- **T18** Docker Compose stack and Playwright suite for AT-01 to AT-18 (#29). Fixed: the agent's "I need a clear answer" was hidden behind a canned message in the UI.

### Application
- **T17** React sign-in and chat UI, served by the API (#22).
- **T16** Chat API (#21). **T15** FastAPI app, demo users, sessions, health checks (#20).
- **T14b** All flows connected in one agent (#19). **T14a** Typed state and PostgreSQL checkpointing (#17).
- **T13** Intent classification: JEV with OpenAI fallback (#16).
- **T12** Ticket creation with preview, strict confirmation and duplicate protection (#18).
- **T11** Ticket status lookup and ticket picking (#15). **T10** Ticket adapter interface and hardened Jira adapter (#14).

### Knowledge and retrieval
- **T09** Answerability check and grounded answers with sources (#13).
- **T08** Retriever, evaluation cases, retrieval inspection (#12). **T07** Repeatable index build (#11).
- **T06 / T06b** Typed knowledge-base records and loader; label-only entries removed (#9, #10).
- **T05 / T05b** Synthetic onboarding, payroll and HR policy entries; borrowed company identity removed (#7, #8).
- **T04** Knowledge Gap topics defined and matching content pruned (#6). **T03** Knowledge base limited to redistributable sources (#5).

### Foundations
- **T02** Pull request CI and repository protections (#2). **T01** Repository layout and tooling (#1).
- Project plan, backlog, glossary and ADRs 0001 and 0002.

### Known gaps
Not exercised with real credentials: OpenAI, JEV, Jira. No Azure deployment has been made. No answer-quality results recorded. See the README and [design decisions](docs/design-decisions.md).
