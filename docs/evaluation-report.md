# Evaluation report

**Status: measured with the real models on 2026-11-07** (OpenAI `gpt-4o-mini` for answers, `text-embedding-3-small` for embeddings, intent classification through JEV (confirmed live: results report `source='jev'`) with OpenAI as the fallback, the real 20,935-record index). Commit: `main` at `f1c2164` plus the pull request that adds this report (`flow-cancel-midway`, the intent definitions and `--search-mode` are in it). Raw outputs: [`docs/evaluation-runs/`](evaluation-runs/).

## Results

`scripts/run_eval.py --providers real`, once per search mode. Tickets go to an in-memory fake ticket system, the answer cache is off, so latency and cost are honest.

| Measure | Dense search | Hybrid (SPLADE + reranker) |
|---|---|---|
| Retrieval Recall@5 / Hit@5 / MRR (44 labelled questions) | 0.856 / 0.864 / 0.756 | **0.981 / 1.000 / 0.910** |
| Answerable questions answered (44) | 81.8% | **100.0%** |
| Answered and cited an expected source (groundedness proxy) | 81.8% | **100.0%** |
| Answerable questions wrongly sent to a ticket offer | 18.2% | **0.0%** |
| Knowledge-gap questions escalated (42, of 14 missing topics) | 100.0% | 100.0% |
| Knowledge-gap questions answered anyway | 0.0% | 0.0% |
| Routing accuracy (103 checks) | 100.0% | 100.0% |
| Ticket flows passed (5) | 100.0% | 100.0% |
| Status lookups passed (4: none, one, several tickets; other users' tickets hidden) | 100.0% | 100.0% |
| Safety cases passed (4: no ticket before confirmation, injection, bare "yes") | 100.0% | 100.0% |
| Latency per turn, p50 / p95 (120 turns) | 0.85 s / 4.7 s | 1.03 s / 5.9 s |
| Model calls / estimated cost for the whole run | 91 chat, 174 embedding / $0.010 | 107 chat, 174 embedding / $0.013 |

Retrieval figures move slightly between runs (Recall@5 for dense was 0.856 to 0.879 across three runs) because the embedding service does not return bit-identical vectors every time. Treat the third decimal as noise.

### Answer quality against reference answers (LangSmith)

`tier0-qa`: 191 questions written from the knowledge base with a reference answer, key facts and correct sources (149 answerable, 42 not); see [LangSmith](langsmith.md#the-question-and-answer-set). A judge model (`gpt-4o-mini`) checks each reply against the key facts.

| Measure (experiment) | Dense | Hybrid |
|---|---|---|
| Behaved correctly: answered what it should, offered a ticket for what it should not (191) | 0.864 | **0.963** |
| Cited a correct source (149 answerable) | 0.752 | **0.899** |
| Key-fact coverage: share of the reference's key facts the reply states | 0.700 | **0.824** |
| Correct: every key fact stated and no contradiction | 0.591 | **0.651** |

Experiments `submission-dense-qa-4e4c4f96` and `submission-hybrid_rerank-qa-d30aede6` in the LangSmith project. "Correct" is deliberately strict, and the reference answers and the judge are both models, so read it as a comparison between the two modes and a floor for quality, not as an accuracy percentage. Most misses are IT-ticket questions whose knowledge-base entries are terse support notes.

### What the evaluation found, and what was fixed

The first real run (before the changes below) scored:

| | First run | After the fixes |
|---|---|---|
| Routing accuracy | 92.2% | 100.0% |
| Knowledge-gap questions escalated | 83.3% | 100.0% |
| Ticket flows passed | 80.0% | 100.0% |

- **Seven gap questions were not escalated.** Two statements of a problem ("The screen on my external display flickers every few seconds.", "The projector in the meeting room will not turn on.") were classed as ambiguous and met with a clarifying question; five workplace questions (office parking, cafeteria menu, canteen hours, vending machine, shuttle) were classed as out of scope and politely declined. The specification names office parking as a knowledge gap that must lead to a ticket offer, and an unresolved support issue should be offered escalation (FR-ORCH-05). The intent definitions now say that a described IT or HR problem and questions about the workplace are knowledge questions, so the knowledge base is tried and, finding nothing, a ticket is offered. Genuinely unrelated requests (recipes, weather, trivia, code) are still declined.
- **`flow-cancel-midway` was reported as a failure** though the agent behaved correctly (the employee says "never mind" while the agent collects details, and no ticket is created). The harness insisted every ticket flow reach the preview; it now accepts a clean back-out when the case expects no tickets.
- **Search quality was the main cause of unanswered questions** (18% under dense search); hybrid search removes it. See [hybrid search](hybrid-search.md).

## Live Jira

The Jira adapter has been used against a real Jira Cloud project (key `SUP`), not only the fake: employees created tickets through the app, and `scripts/jira_smoke.py alice@buzztech.example` (read-only) found them again on 2026-11-07 through the requester and environment-label filter:

```
Project SUP, environment label tier0-env:local
2 ticket(s) for alice@buzztech.example:
  SUP-16  [Open]  Keyboard has stopped working
  SUP-15  [Open]  Monitor flickers when docked
```

Creation, the duplicate-protection label, lookup by requester, status and public comments are therefore exercised against the real service; the failure paths (timeouts, rejected fields, unknown outcomes) are exercised against recorded and simulated responses, since a live Jira cannot be made to fail on demand.

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
OPENAI_API_KEY=... uv run python scripts/run_eval.py --providers real --search-mode hybrid_rerank --index-dir .data/chroma --out eval-out
```

In GitHub, **Actions, Evaluation** runs nightly at 03:17 UTC and on demand (optionally limiting the knowledge conversations to cap cost). It needs `OPENAI_API_KEY` (and `TYPESAFE_API_KEY`) in the `staging` environment; without them it skips with a warning instead of failing. The summary appears on the run page and the full `summary.json` / `summary.md` are kept as an artifact for 90 days. Scheduled workflows pause after 60 days without repository activity.

## Known limits of these numbers

- **Small sets.** 44 answerable and 42 unanswerable questions (14 topics), 25 scripted conversations, 191 reference-answer questions. A difference of one or two questions is not a finding.
- **The answerable questions are the ones used to choose the thresholds** (0.30 cosine, 0.12 reranker score) and to tune the intent definitions, so the headline rates are optimistic for unseen questions. The 191-question set was generated afterwards from the knowledge base and is the fairer test (65% strict correctness, 90% correct citation).
- **Groundedness is a proxy** (the cited source is a labelled relevant article), plus the judge model's key-fact check. There is no sentence-by-sentence faithfulness metric.
- **Cost is an estimate** (characters/4 as tokens, placeholder prices; JEV calls are not counted) and the model-call counts do not include intent classification or the judge.
- **Tickets are fake in these runs.** Real Jira behaviour is shown separately above.
- **Latency** is from one laptop on a home network; hybrid search adds roughly 0.2 s per question on CPU.

## Results log

| Date | Search | Where |
|---|---|---|
| 2026-11-07 | dense | [summary](evaluation-runs/2026-11-07-dense.md), [json](evaluation-runs/2026-11-07-dense.json) |
| 2026-11-07 | hybrid_rerank | [summary](evaluation-runs/2026-11-07-hybrid_rerank.md), [json](evaluation-runs/2026-11-07-hybrid_rerank.json) |
