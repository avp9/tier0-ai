# Hybrid search: SPLADE and a reranker

`SEARCH_MODE=hybrid_rerank` makes the knowledge-base search better at exact words and at picking the right article from several similar ones. `SEARCH_MODE=dense` (the default) is the original embedding-only search.

```
question
  ├─ embedding search (OpenAI + Chroma)     top 20 ┐
  └─ SPLADE keyword search (local, in memory) top 20 ┴─ merged by rank fusion ─ top 20
                                                         │
                                  cross-encoder reads each of the 20 with the question
                                                         │
                          best 5, scored 0 to 1  ─►  "can we answer this?" check ─► answer
```

- **SPLADE** gives every text a weight for each word in a 30,000-word vocabulary, including related words it never used. Matching is a sum over shared words, so an exact term, name or code counts for a lot, which dense embeddings tend to blur.
- **Rank fusion** keeps an article that either search likes. It does not help much alone; its job is to hand the reranker a better set of candidates (recall@5 reaches 0.98).
- **The reranker** (`ms-marco-MiniLM-L-6-v2`, 80 MB) reads question and article together. It decides the final order and its 0 to 1 score replaces the cosine score everywhere (sources, the header search check, the answer bar).

Both models run on CPU through ONNX Runtime (via `fastembed`), so the code, the models and the results are identical on a Mac (Apple Silicon or Intel) and on Linux in Azure. No PyTorch, no GPU.

## Results (44 evaluation questions, real index of 20,935 articles)

| | hit@1 | hit@3 | hit@5 | recall@5 | MRR |
|---|---|---|---|---|---|
| `dense` | 0.68 | 0.86 | 0.89 | 0.88 | 0.77 |
| `hybrid_rerank` | **0.84** | **0.98** | **1.00** | **0.98** | **0.91** |

(`uv run python scripts/inspect_retrieval.py --evaluate [--search-mode hybrid_rerank]`. The LangSmith retrieval experiment uses slightly different counting, so its dense hit@1 is 0.66; see [langsmith.md](langsmith.md).)

Through the whole agent (`scripts/run_eval.py --providers real`, 2026-11-07, real OpenAI models; details in the [evaluation report](evaluation-report.md)):

| | dense | hybrid_rerank |
|---|---|---|
| Questions answered with a correct article cited (44) | 82% | **100%** |
| Questions the knowledge base cannot answer that were sent to a ticket offer (42) | 100% | 100% |
| Reference-answer set (191): cited a correct source / every key fact stated | 0.75 / 0.59 | **0.90 / 0.65** |

## Comparing the variants yourself

`scripts/compare_search.py` runs the same questions through five variants: dense, SPLADE alone, dense + SPLADE merged, dense + reranker, and the full pipeline.

```
uv run --env-file ../.env python scripts/compare_search.py --queries     # table, and who got each question right
uv run --env-file ../.env python scripts/compare_search.py "hotel limit for business travel"   # top 3 of each
```

| | hit@1 | hit@3 | hit@5 | recall@5 | MRR |
|---|---|---|---|---|---|
| dense | 0.659 | 0.818 | 0.841 | 0.833 | 0.733 |
| SPLADE alone | 0.841 | 0.955 | 0.977 | 0.958 | 0.896 |
| dense + SPLADE merged | 0.841 | 0.932 | 0.955 | 0.947 | 0.881 |
| dense + reranker | 0.705 | 0.841 | 0.864 | 0.845 | 0.773 |
| **dense + SPLADE + reranker** | **0.841** | **0.977** | **1.000** | **0.981** | **0.910** |

What it shows: SPLADE is the biggest single gain; merging with dense mostly adds candidates; the reranker on dense candidates alone gains little, because the right article is often not among them; the reranker on the merged candidates is what reaches 1.00 at 5. On hit@1 SPLADE alone ties the full pipeline, so the full pipeline's advantage is lower down the list and in the answer bar. Five questions (ret-028, 035, 038, 040, 042) are missed at rank 1 by every variant.

In LangSmith, `scripts/langsmith_eval.py --search-mode dense|hybrid_rerank run retrieval|knowledge|gaps` gives the dense vs full comparison with every trace, over time.

## Sample queries

Run any of them with `uv run python scripts/inspect_retrieval.py "<question>" --search-mode hybrid_rerank` (add nothing for the dense search).

| Question | Dense search puts first | Hybrid puts first |
|---|---|---|
| What is the hotel limit for business travel? | *Google Drive for Business Not Opening* (0.31) | **Travel Expense Rules** |
| How many public holidays does BuzzTech observe? | *Third-party tool integration issues with HubSpot* | **Public Holidays** |
| Who is my buddy and what do they help with? | *Assistance with advanced features of VPChat* | **Buddy Programme** |
| How long do I have to enroll in health insurance after joining? | *Processing PIV card request for expired card replacement* | **Benefits Enrollment Window** |
| reimbursement limit for client dinner | *Client API rate limit issue* | **Client Meals and Team Events** |
| When do I need to submit my tax declaration? | *Changing Your Tax Declaration Mid-Year* | **Annual Tax Declaration** |
| VPN keeps disconnecting | *IPsec VPN disconnects under load* | **Reported VPN instability** (the other still second) |

The first five show what SPLADE adds: the dense search is pulled to articles that merely share a word like "business" or "limit", while the keyword search weights the words that matter (*hotel*, *holidays*, *buddy*, *enroll*, *dinner*) and related ones (*travel* ↔ *expense*, *dinner* ↔ *meals*).

It is not better everywhere. "What is the notice period?" now puts *Probation Confirmation and Leave During Probation* first and *Resignation and Notice Period* second (dense had them the other way round). On the 44 questions no case that dense answered at rank 1 got worse, but a few questions like this exist.

## The answer bar

`RERANK_SCORE_THRESHOLD` (default 0.12) replaces `RETRIEVAL_SCORE_THRESHOLD` in this mode. The reranker separates answerable questions from ones the knowledge base cannot answer much better than cosine similarity does: on the check set 0.12 keeps every answerable question and rejects 88% of the gap questions (the LLM sufficiency check handles the rest, as before). The header's search check shows the suggestion for the current mode; for example 0.905 keeps 95% of answerable and rejects 98% of gap questions, a stricter choice.

## Building the index

`scripts/build_index.py` now also builds the keyword index, in `<index>/sparse/` (`vectors.npz` and `meta.json`, 26 MB for 20,935 articles). Encoding the articles is the slow part: about 13 minutes on a laptop for the full knowledge base, and none when nothing changed (it is keyed to the index hash and the model). `--sparse none` skips it.

The CI cache key (`--print-hash`) includes the keyword model, so the nightly and deploy workflows rebuild when it changes. The first deploy after this change builds the keyword index on a GitHub runner, which is slower than a laptop.

## Running it

Locally: `SEARCH_MODE=hybrid_rerank` in `.env`, run `build_index.py` once, and start the server as usual. The models (about 600 MB: SPLADE is about 500 MB, the reranker 90 MB) download on first start into `MODEL_CACHE_DIR` (or fastembed's default folder).

In Azure: the image sets `SEARCH_MODE=hybrid_rerank`, bakes the models in (`HF_HUB_OFFLINE=1`, so nothing is fetched at runtime), and the container has 1 vCPU / 2 GiB (the models take about 1 GB once loaded; the old 1 GiB was too tight). Set `SEARCH_MODE=dense` as an environment variable to switch back without a rebuild.

If the keyword index or a model is missing or fails to load, the app logs `SEARCH_MODE=hybrid_rerank is not available` and uses the dense search with its own threshold. If a model fails during one question, that question is answered from the dense search. The answer cache is namespaced by search mode and model, so the two never share answers.

## Cost

Image: about +640 MB (models 595 MB, Python packages +40 MB), so roughly 1.85 GB instead of 1.21 GB.

Per question: SPLADE about 4 ms, reranking 20 articles about 55 ms on a laptop CPU. In the container the same work shares one vCPU with the app, so expect roughly double.
