# 2. The Chroma index is built in CI and baked into the container image

**Status:** Accepted (2026-11-05)

## Context

The knowledge base is ~21k entries (`qa_dataset.json.gz` plus synthetic Onboarding/Payroll content). Embedding it with OpenAI takes several minutes and costs a small amount per run. Chroma persists its index as local files backed by SQLite. On Azure Container Apps, container filesystems are ephemeral, and the hosting budget should stay as low as possible.

Alternatives considered:

- **Azure Files mount** — persistent, but SQLite over SMB network shares is prone to locking and corruption.
- **Chroma server as a separate container app** — robust, but an extra always-on service, more cost, and more moving parts.
- **pgvector in the existing Postgres** — viable, but the existing code and the chosen stack use Chroma.
- **Bake into the image** — chosen.

## Decision

A CI job builds the Chroma index from the knowledge-base files and copies it into the application image. The job is keyed on a hash of the knowledge-base sources and the embedding model, so the (paid) embedding step only reruns when either changes. At runtime the index is read-only.

## Consequences

- No extra Azure service; staging and production get an identical, versioned index with each image.
- Any knowledge-base change requires a new image and deploy. That's acceptable because content changes go through PRs anyway.
- The image is larger (index on the order of 100-200 MB for about 21k vectors; measure it in T19), which lengthens cold starts after scale-to-zero.
- Building the index needs the OpenAI key in CI, but only on the main-branch build path, never on PRs from forks.
- The app cannot add knowledge at runtime. If that becomes a requirement, revisit with a Chroma server or pgvector.
