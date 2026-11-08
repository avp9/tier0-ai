# Design decisions

The specification (`SmartDesk_AI_Complete_Specification.md`) is followed except where listed here. Each entry says what the specification suggested, what was built, and why. Formal records: [ADR 0001](adr/0001-signed-in-email-is-requester-email.md), [ADR 0002](adr/0002-chroma-index-baked-into-image.md).

## Departures from the specification

| Specification | What was built | Why |
|---|---|---|
| Ask the employee for their email when needed (FR-STATUS-01, AT-04, AT-09) | Employees **sign in**; the session's email is always the requester, and the agent never asks for or accepts an email typed in chat | On a public URL, "type an email" lets anyone read another person's tickets, a risk the specification itself notes (FR-STATUS-06). **AT-04 and AT-09 are reinterpreted:** the expected result becomes "the agent does not ask, and uses the signed-in identity". The browser tests check exactly that. Demo accounts are the cheapest sign-in a grader can use; Microsoft Entra ID would be stronger and only changes where the email comes from |
| UI: Gradio or Streamlit suggested (and the spec's prototype used Gradio) | React, TypeScript and Vite, served by the FastAPI backend | React was the choice for the real application: typed, testable (229 component tests), accessible (axe-checked in a real browser), and one deployable unit. The cost is more code than a Gradio script |
| Conversation memory could stay in process (`MemorySaver`) | LangGraph `PostgresSaver` | A conversation must survive restarts and more than one replica, and a pending confirmation must not be lost |
| Local JSON or SQLite map from email to ticket IDs suggested for status lookups | Jira's query language filters by the requester field and environment label | No second copy of ticket data to keep in sync; the requester is stored on the ticket itself |
| A mock ticket service is permitted | A real Jira adapter, **plus** a fake used by tests, the demo stack and the evaluation | The real adapter is the product; the fake gives deterministic tests. **The real adapter has been run against a live Jira Cloud project** (SUP): tickets created through the app are read back by `scripts/jira_smoke.py`, see the [evaluation report](evaluation-report.md#live-jira) |
| Dense retrieval first; hybrid is a bonus | Dense (OpenAI embeddings in ChromaDB) **and** SPLADE keyword search merged by rank fusion, then re-read by a cross-encoder reranker (`SEARCH_MODE=hybrid_rerank`) | Measured on the 44 labelled questions: hit@1 0.68 → 0.84, hit@5 0.89 → 1.00, and the agent answers 100% instead of 82% of them with a correct citation. Both models run on CPU through ONNX Runtime, the same on a Mac and in Azure. See [hybrid search](hybrid-search.md) |
| ChromaDB for retrieval | ChromaDB, but the index is built in CI and baked into the image (ADR 0002) | Container filesystems are ephemeral and a network share is risky for SQLite; an extra index server costs more. Consequence: a knowledge change needs a new image |
| Evaluation optional | A harness, a nightly workflow, LangSmith datasets and experiments, a 191-question reference-answer set, and a report with **measured results from real models** | See the [evaluation report](evaluation-report.md) and [LangSmith](langsmith.md) |
| Cross-session memory and multi-agent (bonus) | **Not built** | Optional, and the baseline was finished first. Conversations persist and can be resumed (history sidebar), but the agent does not remember an employee across new conversations. IT and HR share one agent, one retrieval index and one set of tools; a split would add routing and cost without a measured need |

## Chunking strategy

**Records are indexed whole; there is no splitting.** The knowledge base is made of self-contained units: a question with its answer (465 HR policy Q&A pairs, 20,414 IT ticket resolutions) or one short policy article (56 synthetic handbook, onboarding, payroll and equity articles). Measured sizes in characters (median, 95th percentile, longest): handbook articles 332 / 394 / 438, HR Q&A 335 / 488 / 776, IT tickets 763 / 1,048 / 1,561. The longest record is about 400 tokens, well inside the embedding model's limit (8,191 tokens) and the 512-token limit of the SPLADE and reranker models, so nothing is truncated.

Why not split them:
- **A split would separate a question from its answer, or a step from its context.** A passage that says "restart the client" without saying which client no longer answers anything.
- **One record is one citation.** The answer cites the article's title and id, and the evaluation checks that cited id. Chunks of one article would need merging back before they could be cited.
- **No benefit to pay for.** Fixed-size chunks with overlap help when documents are many pages long; none here are. More, smaller vectors would only add near-duplicates to the search results.

What was done instead: each Q&A row becomes one record laid out as `Question: … Answer: …` with an id derived from its content (`rag/loader.py`); borrowed rows are cleaned of the original company's names, e-mail addresses and entries that would contradict the synthetic handbook (`rag/normalize.py`); every record is validated against a schema (`rag/schema.py`); and the index is keyed by a content hash, so an unchanged knowledge base is not rebuilt. If longer documents (policies as PDFs, say) are added, a splitter by heading with a size cap and a small overlap belongs in the loader; the index, retrieval and citation code would not change, as each chunk would simply be another record with its parent's title.

## Choices the specification left open

- **Strict confirmation in code.** The decision "did the employee clearly say yes?" is made by a deterministic parser, not the model, because a model can be talked into agreeing. The price is that unusual but sincere phrasings ("sure, why not") get a polite re-ask.
- **Answer only when the evidence is there.** An answer needs both a similarity score above a threshold and a second model check that the passages really answer the question; otherwise the agent says so and offers a ticket. Deliberately removed topics ("Knowledge Gaps") test this. The thresholds (0.30 on cosine similarity for dense search, 0.12 on the reranker's 0 to 1 score for hybrid) were chosen from the labelled questions; see the evaluation report for what they keep and reject.
- **Sources come from retrieval, never from the model.** Citations are looked up by ID in what was retrieved, so a model cannot invent one.
- **Two intent classifiers.** JEV is the primary (the existing integration) with OpenAI structured output as the fallback, so one provider outage does not stop the agent. The price is two keys and two failure modes to test.
- **Duplicate-proof submission** with a stored submission ID, a Jira label and a "look before creating" rule (see [architecture](architecture.md)). A timeout is treated as "unknown", which is safer than "failed".
- **Fakes are guarded.** Fake providers and test endpoints cannot be enabled outside `local`/`e2e`; the server refuses to start with them anywhere else.
- **Fail fast in deployments.** Outside local development a missing setting stops start-up instead of producing a half-working chat.
- **No message text in logs**, emails masked, every request traceable by ID ([observability](observability.md)).
- **One replica per environment**, because rate limits are counted in memory. Simpler and cheaper; a shared store (for example Redis) would be needed to scale out.
- **Hosting:** Azure Container Apps on the Consumption plan plus PostgreSQL Flexible Server B1ms, chosen as the cheapest workable option (about $16 to 24 a month at 1 vCPU / 2 GiB, from Azure's price list). The database is the only always-on cost. For a demo or a grader, `scripts/deploy-azure.sh` instead starts the same containers on one small VM.
- **Process:** one small pull request per backlog item, required checks, and a human approval before every merge and before production deploys.

## Trade-offs and limits to be aware of

- Real OpenAI, JEV (Typesafe) and a live Jira Cloud project have been exercised (see the evaluation report). **Azure has not**: the Azure pipelines and `scripts/deploy-azure.sh` have never created real resources.
- The knowledge base is mostly past IT ticket resolutions rather than policy documents, so answers can read like support notes. Synthetic HR, onboarding and payroll entries were added to cover the missing domains.
- Fake-provider answer text in the demo is a stand-in model quoting the knowledge base, not an indication of real quality.
- Open security items are listed in [failure and adversarial testing](failure-and-adversarial-testing.md).
- The repository has no licence file for its own code; the licences of the datasets it uses are in `data/knowledge_base/SOURCES.md`.
