# Evaluation report

**Status: no run with the real language models has been made yet.** The harness is built and tested, but it needs `OPENAI_API_KEY` (and optionally `TYPESAFE_API_KEY`) and the real knowledge-base index, which have not been available in development. Until a real run is recorded below, this project has **no measured answer-quality numbers**, and none should be quoted.

## What is measured

`backend/scripts/run_eval.py` drives the real agent the way an employee would and reports:

| Measure | How it is computed |
|---|---|
| Retrieval Recall@k, Hit@k, MRR | The 44 labelled questions in `data/evaluation/retrieval_cases.json` against the index |
| Score separation | Highest top score of a knowledge-gap question vs the lowest of an answerable one; "separable" means one threshold could split them |
| Answered / cited an expected source | Each retrieval question is asked as a conversation. "Answered" means a reply with sources and no ticket offer; "cited expected source" means one of those sources is a labelled relevant document. This is a **groundedness proxy**: it shows the answer came from the right document, not that every sentence is supported by it |
| Wrongly escalated | Answerable questions that got a ticket offer instead of an answer |
| Escalation rate / answered anyway | The 42 questions about deliberately missing topics (`knowledge_gaps.json`) should all lead to a ticket offer, never an answer |
| Routing accuracy | Intent chosen vs expected, over the 12 routing cases plus every knowledge and gap question |
| Ticket flows, status lookups, safety | The scripted conversations in `data/evaluation/conversation_cases.json` (confirmation, decline, ambiguous confirmation, status with 0/1/many tickets, other users' tickets and internal comments hidden, prompt-injection attempts, a bare "yes") |
| Latency p50 / p95 | Wall-clock seconds per agent turn |
| Estimated cost | Counted model and embedding traffic, **estimated** as characters / 4 tokens at placeholder prices (`--price-*` options). Calls to JEV are not counted. Treat it as an order of magnitude |

Tickets always go to an in-memory fake ticket system: a nightly job must not file real tickets. Real Jira behaviour is checked separately by `backend/scripts/jira_smoke.py`.

**Hard rule:** the run fails if any safety case fails, for example a ticket existing before the employee confirmed. Quality numbers are reported but are not a gate, because their right thresholds are unknown until the first real run.

## How to run

```bash
# locally, real providers (cost: roughly a few hundred model calls)
cd backend
OPENAI_API_KEY=... uv run python scripts/build_index.py --index-dir .data/chroma
OPENAI_API_KEY=... uv run python scripts/run_eval.py --providers real --index-dir .data/chroma --out eval-out
```

In GitHub, **Actions, Evaluation** runs nightly at 03:17 UTC and on demand (optionally limiting the knowledge conversations to cap cost). It needs `OPENAI_API_KEY` (and `TYPESAFE_API_KEY`) in the `staging` environment; without them it skips with a warning instead of failing. The summary appears on the run page and the full `summary.json` / `summary.md` are kept as an artifact for 90 days. Scheduled workflows pause after 60 days without repository activity.

## What has been verified so far

The harness was run end to end with the fake providers against a full-size (20,935 record) index built with fake embeddings: all 44 + 42 + 25 conversations ran, 188 model calls and 180 embedding calls were counted, the report was produced, and the safety cases passed. The quality numbers from that run are meaningless (the fake embedder and keyword classifier are crude stand-ins and scored poorly, as expected) and are deliberately not reproduced here. It proves the pipeline works, not that the agent is good.

## Results log

_No real runs yet. Paste the table from a real run here, with the date, commit and model names._
