# syntax=docker/dockerfile:1.7
# Tier0 AI: the web app (API + built UI). Targets:
#   e2e  - fixture knowledge base, fake embeddings; used by docker-compose.yml for browser tests.
# (A production target that bakes the real index is added by backlog item T19.)

FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
COPY backend/scripts ./scripts
RUN uv sync --frozen --no-dev
RUN useradd --system --uid 10001 --create-home tier0 && mkdir -p /app/data && chown tier0 /app/data
COPY --from=web /web/dist /app/frontend/dist
ENV STATIC_DIR=/app/frontend/dist HOME=/home/tier0

FROM base AS e2e
COPY data/fixtures /app/data/fixtures
RUN python scripts/build_index.py --embedder fake --kb-dir /app/data/fixtures \
        --index-dir /app/data/chroma \
    && chown -R tier0 /app/data
ENV VECTOR_STORE_PATH=/app/data/chroma
USER tier0
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "--factory", "tier0.api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
