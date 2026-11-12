# syntax=docker/dockerfile:1.7
# Tier0 AI: the web app (API + built UI). Targets:
#   prod - slim runtime with the real, prebuilt knowledge-base index baked in (the default).
#   e2e  - fixture knowledge base with fake embeddings; used by docker-compose.yml.
#
# The index is built outside the image (it needs OPENAI_API_KEY, which must never enter an image
# layer) and supplied as a named build context:
#   uv run python backend/scripts/build_index.py --index-dir /tmp/index
#   docker build --build-context index=/tmp/index --build-arg INDEX_HASH=<content hash> -t tier0 .
# INDEX_HASH (from build_index.py, printed when it finishes) is required: it is recorded as an
# image label, and it makes a build without a fresh index fail even if layers are cached.

FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# Dependencies are installed here so that uv and build tools never reach the runtime image.
FROM python:3.15-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable
# Replaced by --build-context index=<dir>; empty when not supplied (the prod build then fails).
FROM scratch AS index

FROM python:3.15-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/frontend/dist VECTOR_STORE_PATH=/app/data/chroma HOME=/home/tier0
RUN useradd --system --uid 10001 --create-home tier0 && mkdir -p /app/data && chown tier0 /app/data
COPY --from=builder /opt/venv /opt/venv
COPY --from=web /web/dist /app/frontend/dist
WORKDIR /app

FROM runtime AS e2e
COPY backend/scripts /app/scripts
COPY data/fixtures /app/data/fixtures
# The questions behind the header's search check. (Against the fixture knowledge base most answers
# miss, which is fine: the e2e tests only look at how the result is shown.)
COPY data/evaluation /app/data/evaluation
ENV EVALUATION_DIR=/app/data/evaluation
RUN python scripts/build_index.py --embedder fake --kb-dir /app/data/fixtures \
        --index-dir /app/data/chroma \
    && chown -R tier0 /app/data
USER tier0
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "--factory", "tier0.api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]

# A stage of its own, so only the prod image pays for (and waits for) the download.
FROM builder AS models
# The two search models (SPLADE and the reranker, ~600 MB) are downloaded now, so the running
# container never needs the internet and starts without waiting for a download.
RUN /opt/venv/bin/python -c "\
from tier0.rag.rerank import CrossEncoderReranker; from tier0.rag.sparse import SpladeEncoder; \
SpladeEncoder(cache_dir='/opt/models').encode_query('warm up'); \
CrossEncoderReranker(cache_dir='/opt/models').score('warm up', ['warm up'])"

FROM runtime AS prod
# Search with SPLADE and a reranker as well as embeddings. If the index has no keyword part (or
# the models cannot load) the app logs a warning and uses embeddings alone.
COPY --from=models --chown=tier0 /opt/models /opt/models
ENV SEARCH_MODE=hybrid_rerank MODEL_CACHE_DIR=/opt/models HF_HUB_OFFLINE=1
ARG INDEX_HASH
RUN test -n "$INDEX_HASH" \
    || (echo "Set --build-arg INDEX_HASH=<hash of the supplied index>" >&2; exit 1)
LABEL tier0.index-hash=$INDEX_HASH
COPY --from=index --chown=tier0 / /app/data/chroma
RUN test -f /app/data/chroma/chroma.sqlite3 \
    || (echo "No index supplied: build with --build-context index=<dir>" >&2; exit 1)
USER tier0
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "--factory", "tier0.api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]

# The whole app on one machine (scripts/run-local.sh): the search models and the tools to build
# the index are inside, the index itself lives in a volume (so it is built or copied once).
FROM runtime AS local
COPY --from=models --chown=tier0 /opt/models /opt/models
COPY backend/scripts /app/scripts
COPY data/knowledge_base /app/data/knowledge_base
COPY data/evaluation /app/data/evaluation
ENV SEARCH_MODE=hybrid_rerank MODEL_CACHE_DIR=/opt/models HF_HUB_OFFLINE=1 \
    EVALUATION_DIR=/app/data/evaluation
RUN mkdir -p /app/data/chroma && chown -R tier0 /app/data
USER tier0
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=60s --retries=12 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "--factory", "tier0.api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
