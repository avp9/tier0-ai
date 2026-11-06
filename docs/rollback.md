# Deploying and rolling back

## How a release happens

1. A pull request is merged to `main` after CI passes (required checks and your review).
2. **Deploy** starts by itself once CI has succeeded for that commit. It builds one image for the commit (`ghcr.io/<repo>:<full commit SHA>`), reusing the cached knowledge-base index unless the knowledge base changed.
3. The image goes to **staging**. Smoke tests run against it (`deploy/smoke_test.py`: health, readiness including the database schema and index, the web app and its security headers, anonymous chat refused, test controls absent, a wrong password refused, a real sign-in and a knowledge question, no ticket created).
4. The workflow then **waits for your approval** of the `production` environment. Nothing reaches production without it.
5. After approval the **same image** is deployed to production and smoke-tested again. If those tests fail, the workflow puts the previous image back and reports an error.

Deploy a specific earlier commit (it must have passed CI on `main`) with **Actions, Deploy, Run workflow** and the full SHA.

## Rolling back by hand

Every image is tagged with its commit, so rolling back means pointing the app at an older tag:

```bash
# what is running now, and what ran before
az containerapp revision list -n ca-tier0-prod -g rg-tier0 -o table
# put an earlier image back (full commit SHA of a release that was good)
az containerapp update -n ca-tier0-prod -g rg-tier0 --image ghcr.io/avp9/tier0-ai:<sha>
python3 deploy/smoke_test.py https://<prod host>     # with SMOKE_EMAIL and SMOKE_PASSWORD set
```

Or re-run **Deploy** for the older SHA; production approval still applies.

## Database and rollbacks

Migrations run at start-up. The rule for this project is that a migration must stay compatible with the previous release (add, never drop or rename in the same release), so that rolling the image back needs no database change. Only one migration exists so far, and the rule is a review check, not something the pipeline enforces. If a release includes a migration that breaks it, say so in its pull request and plan the rollback before approving.

## When the smoke tests fail

- **Cold start**: the first request after scale-to-zero can take 15 to 30 s; the script waits up to 4 minutes.
- **Not ready (503 from `/api/ready`)**: the database, schema revision or knowledge index is not right. Check the app logs in Log Analytics (`ContainerAppConsoleLogs_CL`).
- **Image pull failed**: the GHCR package must be public (see `docs/azure-setup.md`).
