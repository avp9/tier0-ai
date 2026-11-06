# First real-world run: checklist

Everything so far was proven against fakes. These steps replace the fakes one at a time, cheapest and least risky first, so each problem shows up while it is still small. Stop at the first step that fails and fix it before going on. Commands run from `backend/` unless noted.

## A. On your machine (no Azure, small cost)

- [ ] **1. Build the real index.** `OPENAI_API_KEY=... uv run python scripts/build_index.py`
  Good: it prints `Index built: ~20,9xx records`. Run it again: it should say "up to date, skipped".
- [ ] **2. Measure retrieval and set the answer threshold.** `OPENAI_API_KEY=... uv run python scripts/inspect_retrieval.py --evaluate`
  Good: Recall and Hit@5 are high, and "one threshold separates them" is **yes**. Use the suggested `RETRIEVAL_SCORE_THRESHOLD` instead of the provisional `0.30`. If the scores overlap, the knowledge base or the gap list needs work, not the code.
- [ ] **3. Check Jira, read-only.** In Jira Cloud create a project (default key `SUP`), a bot user with access to only that project, an API token, and a text field named `Requester Email`; note its id (`customfield_...`) and set `JIRA_REQUESTER_FIELD_ID` if it is not `customfield_10062`.
  `JIRA_SERVER=https://<site>.atlassian.net JIRA_USERNAME=<bot email> JIRA_API_TOKEN=... APP_ENV=staging uv run python scripts/jira_smoke.py alice@buzztech.example`
- [ ] **4. Check Jira, creating one ticket.** Same command with `--create`.
  Good: one clearly labelled ticket appears, is read back, and carries the `tier0-env:staging` label. Watch for: a "field cannot be set" error means the Requester Email field is not on the project's create screen; no priority field means leave `JIRA_SET_PRIORITY_FIELD=false`; internal comments must not appear when you add one in Jira (comments are hidden only if Jira marks them `jsdPublic: false`).
- [ ] **5. Chat with everything real.** Run the app as in the README ("Run it with real providers") with `OPENAI_API_KEY`, the Jira settings and, if you have it, `TYPESAFE_API_KEY`. Ask a known question, ask about a missing topic, raise a ticket and confirm it, then ask for its status. Check the ticket in Jira.
  Good: the flows from the demo script behave the same with real wording. Watch for: the agent over-escalating answerable questions (threshold too high) or answering gap topics (too low).
- [ ] **6. Run the evaluation.** `uv run python scripts/run_eval.py --providers real --index-dir ../.data/chroma --out eval-out --max-knowledge 10` first, then without the limit.
  Good: `Safety cases passed` is 100%. Paste the table, date, commit and model names into the results log in `docs/evaluation-report.md` (the only real numbers this project will have) and open a pull request for it. Look at `Scripted cases that failed`: each is either a real weakness or an expectation too strict for the model's wording.

## B. GitHub (free)

- [ ] **7. Add secrets to the `staging` environment** (Settings, Environments): `OPENAI_API_KEY`, optionally `TYPESAFE_API_KEY`. Run **Actions, Evaluation** manually with a small "max knowledge" value.
  Good: a green run with the table in the run summary and a report artifact.
- [ ] **8. Confirm branch protection still lists** backend, frontend, e2e, image, infra, dependency-audit and the two CodeQL checks as required (Settings, Branches).

## C. Azure (costs about $16 to 21 a month from here)

- [ ] **9. Decide to spend.** The database runs all day; see the cost table in `docs/azure-setup.md`. You can stop it or delete the resource group at any time.
- [ ] **10. Run `infra/bootstrap-azure.sh`.** It asks before every step; creates the resource group, the OIDC app registration (no secret) and the GitHub environments.
- [ ] **11. Set the secrets in the GitHub environments** as listed in `docs/azure-setup.md` (`production`: the ten infrastructure secrets; `staging` and `production`: `SMOKE_EMAIL`, `SMOKE_PASSWORD`).
- [ ] **12. Preview, then deploy the infrastructure.** Run **Actions, Infrastructure** (it shows `what-if` first and needs your approval). Good: the Container Apps environment, two apps and the database exist. Watch for: PostgreSQL B1ms or provider registration errors in your region or subscription.
- [ ] **13. First deployment.** Run **Actions, Deploy** for the latest `main` commit (it checks that CI passed). It builds the real index into the image and pushes to GHCR.
  Then make the GHCR package **public** (GitHub, Packages, package settings) so Azure can pull it, and re-run the failed deploy job if it failed on the pull.
- [ ] **14. Watch staging.** Good: the smoke tests pass. Open the staging URL, sign in, ask a question.
- [ ] **15. Check the proxy assumption.** Send a request to staging with a forged header: `curl -H 'X-Forwarded-For: 1.2.3.4' https://<staging host>/api/health`, then look in Log Analytics (query in `docs/observability.md`) at the logged address of a login attempt. It must be **your** address, not `1.2.3.4` and not the same address for every user. If not, set `TRUSTED_PROXY_HOPS` to match what you see (see `docs/failure-and-adversarial-testing.md`).
- [ ] **16. Approve production** in the Deploy workflow when staging looks right, and check the production smoke tests. Try the rollback once on purpose (`docs/rollback.md`) so it is not the first time during a real problem.
- [ ] **17. Check cold start.** Wait for scale-to-zero, then load the page. Plan says 15 to 30 seconds; if that is too slow for the demo, set `productionMinReplicas` to 1.

## D. Housekeeping

- [ ] **18. Choose a licence** for the code and add the file (the README says there is none).
- [ ] **19. Tell me whether `backend/legacy/` can be deleted.**
- [ ] **20. Update the README table** "What is and is not proven" and the "Known gaps" in `CHANGELOG.md` to match what you actually verified.

## If something fails

Give the failing step number and the output (with secrets removed). Logs: locally in the terminal; on Azure in Log Analytics by `request_id` (`docs/observability.md`); in GitHub from the failed run, where the Deploy and Evaluation workflows also upload reports.
