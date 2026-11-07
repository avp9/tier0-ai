# Changelog

All notable changes, newest first. The project follows one small pull request per backlog item ([PRODUCT_BACKLOG.md](PRODUCT_BACKLOG.md)); the number in brackets is the pull request. No release has been tagged or deployed yet.

## Unreleased (first complete version, 2026-11-05 to 2026-11-06)

### After the first version
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
