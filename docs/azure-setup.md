# Azure setup (one-time, owner)

Nothing here has been created yet. Creating these resources starts billing, so it needs your go-ahead. This page covers what gets created, what it costs, the steps only you can do, and how to remove everything.

## What gets created (resource group `rg-tier0`, region `eastus`)

| Resource | Setting | Why |
|---|---|---|
| Log Analytics workspace `log-tier0` | Pay-per-GB, 30-day retention, 1 GB/day cap | App logs; the cap prevents a surprise bill |
| Container Apps environment `cae-tier0` | Consumption plan only | Hosts both apps |
| `ca-tier0-staging`, `ca-tier0-prod` | 1 vCPU, 2 GiB, scale to zero, **at most 1 replica** | One image, two environments |
| PostgreSQL Flexible Server | Burstable B1ms, PostgreSQL 16, 32 GB, 7-day backups | Users, conversation checkpoints, ticket submissions |
| Databases `tier0_staging`, `tier0_prod` | | One per environment, same server |

At most one replica because the chat and login rate limits are counted in memory per replica; more replicas would multiply the limits.

## Cost estimate (checked against Azure's public retail price API, eastus, 2026-11-06)

| Item | Price used | Per month |
|---|---|---|
| PostgreSQL B1ms compute | $0.017 per hour × 730 h | $12.41 |
| PostgreSQL storage | 32 GB × $0.115 per GB-month | $3.68 |
| Container Apps (both apps) | vCPU $0.000024/s active, $0.000003/s idle; first 180,000 vCPU-s, 360,000 GiB-s and 2M requests free each month | about $0 to 6 at demo traffic (1 vCPU and 2 GiB, because the search models need about 1 GB) |
| Log Analytics | first 5 GB/month free, then about $2.30/GB; capped at 1 GB/day | about $0 to 2 |
| **Total** | | **about $16 to 24**, plus OpenAI/JEV usage |

Backups up to the provisioned size are included. The database is the only cost that runs all day: Container Apps scale to zero, but PostgreSQL B1ms does not. To pause spending between demo periods, stop the server (`az postgres flexible-server stop`; Azure restarts it after 7 days) or delete the resource group.

## Steps for you

1. **Sign in:** `az login` and `gh auth login`.
2. **Run the setup script:** `infra/bootstrap-azure.sh`. It asks before each step. It registers the resource providers, creates `rg-tier0`, creates an app registration with federated (OIDC) credentials for the `staging` and `production` GitHub environments only, grants it **Contributor on `rg-tier0` only**, creates the GitHub environments (production requires your approval) and stores the Azure IDs as repository variables. No password or secret is created or stored.
3. **Export the secrets** for the first deployment (never commit them). Generate random values for the passwords and session secrets, for example `python3 -c "import secrets;print(secrets.token_urlsafe(48))"`:
   ```bash
   export POSTGRES_ADMIN_PASSWORD=... STAGING_SESSION_SECRET=... PRODUCTION_SESSION_SECRET=...
   export OPENAI_API_KEY=... TYPESAFE_API_KEY=... JIRA_API_TOKEN=...
   export JIRA_SERVER=https://<site>.atlassian.net JIRA_USERNAME=<email>
   export STAGING_DEMO_USERS='[{"email":"alice@buzztech.example","name":"Alice Demo","password":"..."}]'
   export PRODUCTION_DEMO_USERS='[...]'
   ```
4. **Preview, then deploy:**
   ```bash
   az deployment group what-if --resource-group rg-tier0 --parameters infra/main.bicepparam
   az deployment group create  --resource-group rg-tier0 --parameters infra/main.bicepparam
   ```
   The apps start on a placeholder image until the deploy pipeline (T21) pushes the first real one.
5. **After the first image is pushed (T21):** make the GHCR package public (GitHub, Packages, Package settings) so Container Apps can pull it without a credential.

## GitHub environment secrets

The pipeline never asks Azure for a password; it only holds these application secrets, set by you in GitHub (Settings, Environments):

| Environment | Secret | Used for |
|---|---|---|
| `staging` | `OPENAI_API_KEY` | Building the knowledge-base index (only when the knowledge base changed) |
| `staging`, `production` | `SMOKE_EMAIL`, `SMOKE_PASSWORD` | A demo user the smoke tests sign in as (must exist in that environment's `DEMO_USERS`) |
| `production` | the ten secrets in step 3 above | `infra.yml`, which copies them into Container Apps secrets |

Run **Actions, Infrastructure** (manual, needs your production approval) after changing any of them. See `docs/rollback.md` for deploys and rollbacks.

## Re-deploying infrastructure

`az deployment group create` replaces each app's configuration. Pass the images currently running (`stagingImage`, `productionImage`) or the apps go back to the placeholder. The Infrastructure workflow does this automatically; for a manual deployment export `STAGING_IMAGE` and `PRODUCTION_IMAGE` first.

## Known limits

- The database is reachable from any Azure service (the `0.0.0.0` firewall rule), protected by TLS and a long random password. A private network would fix that but adds cost and complexity.
- Both apps connect as the database administrator. Separate least-privilege roles per environment would be better; they are not set up yet.
- Secrets live in Container Apps secrets, set from the deployment's secure parameters. Rotating one means re-running the deployment.
- Cold start after scaling to zero is about 15 to 30 s with the real index. For a demo set `productionMinReplicas` to 1 (about $10 to 15 more per month).

## Removing everything

`az group delete --name rg-tier0` removes all resources. Then `az ad app delete --id <client id>` removes the app registration, and delete the GitHub environments if wanted.
