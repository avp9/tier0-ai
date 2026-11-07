# LangSmith

Optional. With it on, every chat turn is traced, each answer gets a thumbs up/down, and the evaluation questions can be run as experiments you compare over time.

## Turning it on

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...            # required outside local runs when tracing is on
LANGSMITH_AGENT_ID=tier0-support-agent
```

Traces go to the project `<LANGSMITH_AGENT_ID>-<environment>`, where the environment comes from `APP_ENV` (`local`, `staging`, `production`), so for example `tier0-support-agent-local`. In staging and production the API key must be a secret of the deployment; the app refuses to start with tracing on and no key.

`LANGSMITH_AGENT_ENVIRONMENT` is **not** read by the app. Leave it unset or set it to a valid environment (`local`): the LangSmith library reads it itself, and a value such as `stage` makes it trace to a project that does not exist. The app switches the library's own environment-driven tracing off and sends only what it chooses to.

The end-to-end tests never trace.

## What is traced

One trace per turn: the agent graph, the search (with the articles found and their scores), the answerability check, the model calls (with tokens and cost), the answer, and ticket calls. Turns of one conversation are grouped as a LangSmith *thread* (`thread_id`). The turn's id is the trace id, which is how a thumbs vote finds its trace.

## What is hidden

Everything is masked before it leaves the server, in inputs, outputs and metadata: email addresses, long numbers (phone, card, account) and secrets (keys, tokens, passwords). The signed-in employee's email is never attached; traces carry an opaque user id only. Masking is by pattern, so it is a safety net and not a guarantee: free text can still hold names.

## Thumbs up/down

Under each answer. The vote is stored in Postgres (table `answer_feedback`) and attached to the trace as feedback `user_score` (1 or 0) with the optional comment. Changing or withdrawing the vote updates both. A vote is sent with a signed token, tied to the answer and the user, so nobody can rate another person's answer. If LangSmith is down, the vote is still saved.

Thumbs do not come back after a page reload: the tokens are not kept with the conversation history.

## Evaluation experiments

```
cd backend
uv run python scripts/langsmith_eval.py upload                       # make the datasets match data/evaluation
uv run python scripts/langsmith_eval.py run retrieval                # search only: cheap, no model calls
uv run python scripts/langsmith_eval.py run knowledge --prefix after-reranking
uv run python scripts/langsmith_eval.py --limit 5 run gaps           # a small sample
uv run python scripts/langsmith_eval.py run all
```

| Dataset | What is scored |
|---|---|
| `tier0-retrieval` | `hit@1/3/5`, `recall@1/3/5`, `reciprocal_rank` |
| `tier0-knowledge` | `answered`, `cited_correct_article`, `routed_correctly` |
| `tier0-gaps` | `declined_correctly`, `routed_correctly` |
| `tier0-conversations` | `passed` (with the reasons in the comment) |

Each experiment is labelled with the models, top-k, score threshold and git commit, so two experiments can be compared side by side in LangSmith. Tickets always go to an in-memory fake ticket system, never Jira. Use `--providers fake` to run without OpenAI. The commands that use real providers spend OpenAI tokens; `run retrieval` only embeds the questions.
