# syntax=docker/dockerfile:1.7
# Tier0 AI: the web app (API + built UI). Targets:
#   prod - slim runtime with the real, prebuilt knowledge-base index baked in (the default).
#   e2e  - fixture knowledge base with fake embeddings; used by docker-compose.yml.
#
# The index is built outside the image (it needs OPENAI_API_KEY, which must never enter an image
# layer) and supplied as a named build context:
#   uv run python backend/scripts/build_index.py --index-dir /tmp/index
#   docker build --build-context index=/tmp/index -t tier0 .

FROM node:22-bookworm-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
RUN npm run build

# Dependencies are installed here so that uv and build tools never reach the runtime image.
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev --no-editable

# Replaced by --build-context index=<dir>; empty when not supplied (the prod build then fails).
FROM scratch AS index

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    STATIC_DIR=/app/frontend/dist VECTOR_STORE_PATH=/app/data/chroma HOME=/home/tier0
RUN useradd --system --uid 10001 --create-home tier0 && mkdir -p /app/data && chown tier0 /app/data
COPY --from=builder /opt/venv /opt/venv
COPY --from=web /web/dist /app/frontend/dist
WORKDIR /app

FROM runtime AS e2e
COPY backend/scripts /app/scripts
COPY data/fixtures /app/data/fixtures
RUN python scripts/build_index.py --embedder fake --kb-dir /app/data/fixtures \
        --index-dir /app/data/chroma \
    && chown -R tier0 /app/data
USER tier0
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "--factory", "tier0.api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS prod
COPY --from=index --chown=tier0 / /app/data/chroma
RUN test -f /app/data/chroma/chroma.sqlite3 \
    || (echo "No index supplied: build with --build-context index=<dir>" >&2; exit 1)
USER tier0
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health', timeout=2)"
CMD ["uvicorn", "--factory", "tier0.api.app:create_app_from_env", "--host", "0.0.0.0", "--port", "8000"]
