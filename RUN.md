# Running Tier0 AI (start here)

Tier0 is an IT and HR help-desk assistant: employees sign in, ask questions (answered from a knowledge base, with sources), and confirm raising a support ticket when no answer exists.

## What you need

- **Docker** running: Docker Desktop on a Mac or Windows, Docker Engine on Linux. About 4 GB of memory free for it, and about 5 GB of disk.
- **An OpenAI API key** (answers and search embeddings use it; a handful of questions cost a few cents).
- A Mac or Linux terminal (on Windows, use WSL).

## Run it

```bash
unzip tier0-capstone-*.zip && cd tier0-ai
./scripts/run-local.sh
```

The script asks for your keys once and saves them only in `.env` in this folder: `OPENAI_API_KEY` (required), and optionally `TYPESAFE_API_KEY`, `JIRA_API_TOKEN` (it then asks for the Jira site and user) and `LANGSMITH_API_KEY`. Press Enter to skip an optional one. It then builds and starts everything: the web app and API, PostgreSQL and Redis. **The first run takes about 5 to 10 minutes** (it builds the app and downloads two small search models). Later runs take seconds. When it finishes it prints the address and the sign-in:

```
Tier0 is running:  http://localhost:8000
Sign in with:  alice@buzztech.example / alice-demo-pass-1   (or bob@buzztech.example / bob-demo-pass-22)
```

If port 8000 is taken: `./scripts/run-local.sh --port 8001`.

## Try these

- `What is the hotel limit for business travel?` : a knowledge answer with its source
- `Unable to reset password via self-service portal` : an IT answer with its source
- `My monitor keeps flickering, how do I fix it?` : not in the knowledge base, so it offers a ticket; say yes, review the preview, confirm
- `What is the status of my tickets?`

More in [docs/demo-script.md](docs/demo-script.md).

## Notes

- **Tickets:** without Jira credentials in `.env` (`JIRA_SERVER`, `JIRA_USERNAME`, `JIRA_API_TOKEN`) tickets are created in an in-memory stand-in and disappear when the app restarts. With them, real Jira tickets are created.
- **The search index** (about 21,000 articles) comes ready-built in the zip's `.data` folder and is copied in on the first run. If it was left out, the first run builds it: 15 to 40 minutes and a few cents of OpenAI embeddings.
- **Other commands:** `./scripts/run-local.sh logs | status | down` (stop, keeping data) `| reset` (stop and delete all data).
- **No Docker or no key?** The unit tests need neither: see "Run it from source" in [README.md](README.md). 
- **On Azure:** `./scripts/deploy-azure.sh` (needs the Azure CLI; creates a small VM and starts the same containers on it; `destroy` removes it).
- **For marking:** [docs/requirements-map.md](docs/requirements-map.md) lists every requirement, acceptance test, rubric category and bonus with where it is met and how it is proven; [docs/evaluation-report.md](docs/evaluation-report.md) has the measured results.
- Architecture, design decisions and the full configuration table are in [README.md](README.md) and [docs/](docs/).
