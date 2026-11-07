# Answer cache (Redis semantic cache)

When someone asks a question that has already been answered, Tier0 can reuse the stored answer
instead of searching the knowledge base and calling the language model again. It is optional: with
no `REDIS_URL` nothing changes.

An answer that comes from the cache says **Answered from cache** under it.

## How a question is matched

Two steps, cheapest first. Both happen after a follow-up has been rewritten into a standalone
question, so "and what about from home?" is never matched on its own words.

```text
question
  1. exact      the same question, ignoring capitals, punctuation and spacing
                  -> a plain Redis key lookup; no embedding call at all; cannot be wrong
  2. by meaning embed the question (once; the vector is reused for the search below)
                  -> the most similar stored question, if at least SEMANTIC_CACHE_SIMILARITY alike
  miss -> search the knowledge base, check the passages, write the answer -> store it
```

"How do I reset my password?" and "how do i reset my password?!" are the same question (step 1).
"How can I reset my password" is found by meaning (step 2). Everything else is answered normally
and then remembered.

A cache hit skips the knowledge-base search and the two model calls (the check that the passages
answer the question, and the answer itself). The intent check still runs first, because it decides
that this is a knowledge question at all.

## What is and is not stored

- Only real answers. A "no reliable answer, shall I raise a ticket?" reply is never cached, so a
  new article can help the next person, and a model outage is never replayed to anyone.
- Never a question that contains an email address or a long number (a phone, card or account
  number), or one longer than 300 characters. The cache is shared by everyone and lives in Redis
  until it expires, so personal details are kept out of it.
- Entries expire after `SEMANTIC_CACHE_TTL_SECONDS` (a day by default).

## It cannot serve a stale answer

Entries are filed under a fingerprint of everything an answer depends on: the knowledge index
(its content hash and embedding model), the chat model, both prompts, the answer threshold and
`RETRIEVAL_TOP_K`. Rebuild the index, change a model, edit a prompt or change those settings and
the cache starts afresh; the old entries are never read and simply expire.

## It cannot take the chat down

The cache is an optimisation. Redis being unreachable, slow, full or returning something
unreadable means "not in the cache", and the question is answered the normal way. After a failure
the cache is skipped for 30 seconds so that an outage costs nothing per question, then tried
again by itself. Connections time out after `SEMANTIC_CACHE_TIMEOUT_SECONDS` (0.5 s).

## Setting it up

Redis needs the search module (vector search): Redis 8, or Redis Stack.

```bash
docker run -d --name tier0-redis -p 127.0.0.1:6379:6379 redis:8
export REDIS_URL=redis://localhost:6379        # rediss:// for TLS
```

| Setting | Default | Meaning |
|---|---|---|
| `REDIS_URL` | empty (no cache) | Where the cache lives |
| `SEMANTIC_CACHE_SIMILARITY` | `0.88` | How alike two questions must be (0.5 to 1.0) to share an answer, step 2 only |
| `SEMANTIC_CACHE_TTL_SECONDS` | `86400` | How long an answer is kept |
| `SEMANTIC_CACHE_TIMEOUT_SECONDS` | `0.5` | Connect and read timeout for Redis |

Azure: use Azure Managed Redis (it includes the search module). The older Azure Cache for Redis
tiers do not, and the semantic step needs it. The infrastructure templates do not create a Redis
yet.

## Choosing `SEMANTIC_CACHE_SIMILARITY`

Too low and a question gets the answer to a different one; too high and rewordings never match.
The right value depends on the embedding model, so measure it:

```bash
cd backend && uv run python scripts/inspect_semantic_cache.py --current 0.88
```

It scores paraphrase pairs (should match) and look-alike pairs (must not) from
`data/evaluation/cache_pairs.json`. With `text-embedding-3-small`:

| | Scores |
|---|---|
| Same meaning | 0.52 to 0.91 (half of the pairs score above 0.78) |
| Looks alike, different meaning ("hotel limit" / "meal limit", "reset password" / "reset VPN token") | 0.33 to **0.79** |

The two ranges overlap, which is why this model needs a high setting: even "how do i reset my
password?!" against "How do I reset my password?" scores only 0.83. At the default of 0.88 none
of the look-alike pairs match, with a margin of 0.09 over the highest (0.79), and the clearest
rewordings do. That is deliberate: a missed match costs one normal answer, a wrong match gives
someone the wrong answer. The exact step covers repeats of the same question, and the semantic
step is a bonus on top. Add pairs from your own users' questions to the file to tune it further.

## Behaviour you can see

- `POST /api/chat` returns `"cached": true` for a reused answer (also in the final `done` event of
  a streamed reply). A cached answer arrives whole, with nothing streamed.
- Logs say `semantic cache hit` or `semantic cache miss` with the `step` (`exact` or
  `semantic`), never the question.
- The answer is recorded in the conversation like any other.

## Tests

`tests/unit/test_semantic_cache.py` (no Redis), `tests/integration/test_redis_semantic_cache.py`
(a real Redis: set `TIER0_TEST_REDIS_URL`; CI runs it against a Redis 8 service), the API tests in
`tests/integration/test_api_chat.py` and `e2e/tests/cache.spec.ts` (the Compose stack includes a
Redis; the test controls clear the cache between tests).
