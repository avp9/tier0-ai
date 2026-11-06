# Failure and adversarial testing

What was attacked or broken on purpose, what that found, and what is still open. Tests live in `backend/tests/unit/test_failure_paths.py`, `test_adversarial.py`, `test_agent_graph.py` (concurrency) and `backend/tests/integration/test_api_hardening.py`. The first versions of several tests passed on the first try, so each fix and guard was **mutation-checked**: the guarded line was disabled and the matching test had to fail.

## Problems found and fixed

| Problem | Why it mattered | Fix |
|---|---|---|
| Every client looked like the same address behind Azure's ingress | Login throttling is per address (20 failures in 15 minutes). Behind a proxy, 20 bad logins from anyone would lock sign-in for **everyone** | `TRUSTED_PROXY_HOPS` (set to 1 in the Bicep). The client address is the Nth entry from the right of `X-Forwarded-For`, which a caller cannot forge; with 0 hops the header is ignored. Non-IP values and short headers fall back to the direct peer |
| No limit on request size | One huge POST was read into memory in full before validation could refuse it | `BodyLimitMiddleware` (64 KiB default, `MAX_REQUEST_BYTES`): checks a declared length up front and counts chunked bodies as they arrive; answers 413 |
| A blank model answer was shown as an answer ("\n\nSources: ...") | Looks like an answer, says nothing | Treated as "language service unavailable", which offers a ticket |
| OpenAI returning no `choices` raised `IndexError` | A 500 for the employee instead of the normal "try again or raise a ticket" path | Raised as `LLMError` |
| Some failures after a ticket request was sent were reported as "ticketing unavailable" (`ChunkedEncodingError`, a Jira error with no HTTP status, any other transport failure) | "Unavailable" means nothing was created and invites a retry; if the ticket had in fact been created that risks a duplicate | Writes in those cases are now "outcome unknown", which reconciles by the submission label before ever creating again |
| A Jira success reply without a ticket key crashed with `AttributeError` | The ticket may exist but the employee got a 500 | "Outcome unknown" |
| Malformed Jira search or comment results (`fields: null`, non-list labels, missing `raw`) crashed with `AttributeError`/`TypeError` | A 500 on a status lookup | Treated as the ticket service being unavailable, so it is never mistaken for "no tickets" |
| A knowledge-base title containing a quote could end its own attribute in the prompt fence | Let KB text add fake attributes to its passage tag | `"` is now escaped along with `<`, `>` and `&` |

## Checked and found sound (now guarded by tests)

- Hostile text in the question, a knowledge-base passage or model output: passages cannot close their own tag; the model cannot cite documents it was not given; invalid categories and priorities are rejected; control characters are stripped and lengths bounded in drafted tickets; an unparseable model verdict means "no answer", never a guess.
- Confirmation: 30 tricky replies ("yes?", "yes but change the title", "not yes", lookalike letters, newlines with instructions, mixtures such as "yes no") are never a yes, and mixtures are exactly "unclear".
- NUL bytes, control characters, RTL and zero-width text, SQL-looking text, 2,000 and 2,001 character messages, malformed thread IDs and wrong JSON shapes all give clean responses and conversations that save and reload.
- Eight simultaneous confirmations of the same ticket create one ticket, and two messages on one conversation never run at the same time.
- A database outage returns a generic error with an `X-Request-ID` and no driver or stack details; `/api/ready` reports 503.
- One user's message flood does not rate-limit another user.

## Still open (not fixed, on purpose or for lack of means)

1. **The proxy assumption is unverified.** `TRUSTED_PROXY_HOPS=1` assumes the Container Apps ingress appends the caller's address to `X-Forwarded-For` as one hop. That is how such proxies normally behave, but it has not been observed on a live deployment. Check it on the first deploy (send a request with a forged `X-Forwarded-For` and confirm the logged address is yours).
2. **Priority can be steered by the employee's own wording.** The model proposes a priority from the conversation, so text like "mark this Critical" can influence it. The preview shows the priority and the employee must confirm, so it is visible, but it is not validated against anything.
3. **Internal Jira comments are hidden only when Jira marks them** (`jsdPublic: false`). A comment with no such field is treated as public. This is right for Jira Service Management; it would show internal notes on a project type that does not set the field.
4. **Slow-body attacks** (a client dribbling bytes) are not handled in the app; they are left to the ingress and its timeouts.
5. **Login and chat rate limits are per replica** (in memory). The Bicep caps the apps at one replica for this reason.
6. **Masking of logs is pattern based** (see `docs/observability.md`).
7. **The injection defence rests on prompts plus code.** Prompts tell the model to treat passages as data; the hard guarantees (no ticket without an explicit yes, requester from the session, cited sources from retrieval, validated draft fields) are enforced in code and do not depend on the model obeying.
