# Design decisions

The specification (`SmartDesk_AI_Complete_Specification.md`) is followed except where listed here. Each entry says what the specification suggested, what was built, and why. Formal records: [ADR 0001](adr/0001-signed-in-email-is-requester-email.md), [ADR 0002](adr/0002-chroma-index-baked-into-image.md).

## Departures from the specification

| Specification | What was built | Why |
|---|---|---|
| Ask the employee for their email when needed (FR-STATUS-01, AT-04, AT-09) | Employees **sign in**; the session's email is always the requester, and the agent never asks for or accepts an email typed in chat | On a public URL, "type an email" lets anyone read another person's tickets, a risk the specification itself notes (FR-STATUS-06). **AT-04 and AT-09 are reinterpreted:** the expected result becomes "the agent does not ask, and uses the signed-in identity". The browser tests check exactly that. Demo accounts are the cheapest sign-in a grader can use; Microsoft Entra ID would be stronger and only changes where the email comes from |
| UI: Gradio or Streamlit suggested (and the spec's prototype used Gradio) | React, TypeScript and Vite, served by the FastAPI backend | React was the choice for the real application: typed, testable (111 component tests), accessible (axe-checked in a real browser), and one deployable unit. The cost is more code than a Gradio script |
| Conversation memory could stay in process (`MemorySaver`) | LangGraph `PostgresSaver` | A conversation must survive restarts and more than one replica, and a pending confirmation must not be lost |
| Local JSON or SQLite map from email to ticket IDs suggested for status lookups | Jira's query language filters by the requester field and environment label | No second copy of ticket data to keep in sync; the requester is stored on the ticket itself |
| A mock ticket service is permitted | A real Jira adapter, **plus** a fake used by tests, the demo stack and the evaluation | The real adapter is the product; the fake gives deterministic tests. **The real adapter has not been run against a live Jira** (see the README) |
| ChromaDB for retrieval | ChromaDB, but the index is built in CI and baked into the image (ADR 0002) | Container filesystems are ephemeral and a network share is risky for SQLite; an extra index server costs more. Consequence: a knowledge change needs a new image |
| Evaluation optional | A harness, a nightly workflow and a report, **but no real-model results yet** | The tooling exists; the numbers need API keys that were not available |

## Choices the specification left open

- **Strict confirmation in code.** The decision "did the employee clearly say yes?" is made by a deterministic parser, not the model, because a model can be talked into agreeing. The price is that unusual but sincere phrasings ("sure, why not") get a polite re-ask.
- **Answer only when the evidence is there.** An answer needs both a similarity score above a threshold and a second model check that the passages really answer the question; otherwise the agent says so and offers a ticket. Deliberately removed topics ("Knowledge Gaps") test this. The threshold is provisional until tuned with real embeddings.
- **Sources come from retrieval, never from the model.** Citations are looked up by ID in what was retrieved, so a model cannot invent one.
- **Two intent classifiers.** JEV is the primary (the existing integration) with OpenAI structured output as the fallback, so one provider outage does not stop the agent. The price is two keys and two failure modes to test.
- **Duplicate-proof submission** with a stored submission ID, a Jira label and a "look before creating" rule (see [architecture](architecture.md)). A timeout is treated as "unknown", which is safer than "failed".
- **Fakes are guarded.** Fake providers and test endpoints cannot be enabled outside `local`/`e2e`; the server refuses to start with them anywhere else.
- **Fail fast in deployments.** Outside local development a missing setting stops start-up instead of producing a half-working chat.
- **No message text in logs**, emails masked, every request traceable by ID ([observability](observability.md)).
- **One replica per environment**, because rate limits are counted in memory. Simpler and cheaper; a shared store (for example Redis) would be needed to scale out.
- **Hosting:** Azure Container Apps on the Consumption plan plus PostgreSQL Flexible Server B1ms, chosen as the cheapest workable option (about $16 to 21 a month, from Azure's price list). The database is the only always-on cost.
- **Process:** one small pull request per backlog item, required checks, and a human approval before every merge and before production deploys.

## Trade-offs and limits to be aware of

- Nothing has run against real OpenAI, JEV, Jira or Azure; those are the first things to verify.
- The knowledge base is mostly past IT ticket resolutions rather than policy documents, so answers can read like support notes. Synthetic HR, onboarding and payroll entries were added to cover the missing domains.
- Fake-provider answer text in the demo is a stand-in model quoting the knowledge base, not an indication of real quality.
- Open security items are listed in [failure and adversarial testing](failure-and-adversarial-testing.md).
- The repository has no licence file for its own code, and `backend/legacy/` holds the original prototype scripts, kept only until you confirm they can go.
