#!/usr/bin/env bash
# Start (or stop) the four app instances the feature tour records, on this machine:
#   8101  real models, hybrid search (SPLADE + reranker), JEV, Redis answer cache
#   8102  the same with dense search only (the "before")
#   8103  the same, but with an invalid JEV key (to show the OpenAI fallback)
#   8104  fake models and ticket system with the e2e controls (seeded tickets, injected failures)
# Needs: Docker, a built frontend (cd frontend && npm run build), .data/chroma (the real index),
# .data/chroma-fixture, and OPENAI_API_KEY (and TYPESAFE_API_KEY) in .env.
#   e2e/demo/tour-stack.sh up | down | reset      (reset empties the answer cache before a recording)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/backend"
PIDS="$ROOT/.run-tour.pids"
DB="postgresql://tier0:tier0password@127.0.0.1:55432/tier0"
SECRET="tour-session-secret-0123456789-0123456789-0123456789"
USERS='[{"email":"alice@buzztech.example","name":"Alice Demo","password":"alice-demo-pass-1","gender":"female"},{"email":"bob@buzztech.example","name":"Bob Demo","password":"bob-demo-pass-22","gender":"male"}]'

env_value() { python3 - "$1" <<'PY'
import sys
key = sys.argv[1]
for line in open("../.env"):
    if line.startswith(key + "="):
        print(line.split("=", 1)[1].strip().strip("'\""))
        break
PY
}

case "${1:-up}" in
  up)
    docker start tier0-pg >/dev/null 2>&1 || true
    docker rm -f tour-redis >/dev/null 2>&1 || true
    docker run -d --name tour-redis -p 127.0.0.1:6390:6379 redis:8 >/dev/null
    for _ in $(seq 1 30); do docker exec tour-redis redis-cli ping >/dev/null 2>&1 && break; sleep 1; done
    OPENAI_API_KEY="$(env_value OPENAI_API_KEY)"; TYPESAFE_API_KEY="$(env_value TYPESAFE_API_KEY)"
    OPENAI_MODEL_NAME="$(env_value OPENAI_MODEL_NAME)"
    : > "$PIDS"
    real() { # real PORT SEARCH_MODE JEV_KEY
      APP_ENV=local LLM_PROVIDER=openai TICKETING_PROVIDER=fake DATABASE_URL="$DB" SESSION_SECRET="$SECRET" \
      DEMO_USERS="$USERS" OPENAI_API_KEY="$OPENAI_API_KEY" OPENAI_MODEL_NAME="${OPENAI_MODEL_NAME:-gpt-4o-mini}" \
      TYPESAFE_API_KEY="$3" SEARCH_MODE="$2" REDIS_URL="redis://127.0.0.1:6390" \
      VECTOR_STORE_PATH="$ROOT/.data/chroma" STATIC_DIR="$ROOT/frontend/dist" LANGSMITH_TRACING=false \
      EVALUATION_DIR="$ROOT/data/evaluation" LOG_LEVEL=WARNING \
      uv run uvicorn --factory tier0.api.app:create_app_from_env --port "$1" > "/tmp/tour-$1.log" 2>&1 &
      echo $! >> "$PIDS"
    }
    real 8101 hybrid_rerank "$TYPESAFE_API_KEY"
    real 8102 dense "$TYPESAFE_API_KEY"
    real 8103 hybrid_rerank "invalid-key-to-show-the-fallback"
    APP_ENV=e2e E2E_TOKEN=tour-token LLM_PROVIDER=fake TICKETING_PROVIDER=fake DATABASE_URL="$DB" \
      SESSION_SECRET="$SECRET" DEMO_USERS="$USERS" VECTOR_STORE_PATH="$ROOT/.data/chroma-fixture" \
      STATIC_DIR="$ROOT/frontend/dist" EVALUATION_DIR="$ROOT/data/evaluation" LOG_LEVEL=WARNING \
      uv run uvicorn --factory tier0.api.app:create_app_from_env --port 8104 > /tmp/tour-8104.log 2>&1 &
    echo $! >> "$PIDS"
    for port in 8101 8102 8103 8104; do
      for _ in $(seq 1 90); do curl -sf "http://127.0.0.1:$port/api/health" >/dev/null && break; sleep 2; done
      curl -sf "http://127.0.0.1:$port/api/health" >/dev/null && echo "$port ready" || { echo "$port FAILED"; tail -5 "/tmp/tour-$port.log"; }
    done
    ;;
  reset)
    # Only the cached entries: dropping the search index would leave the running apps with a stale handle.
    docker exec tour-redis sh -c 'redis-cli --scan --pattern "*semcache*" | xargs -r redis-cli del' >/dev/null
    echo "answer cache emptied ($(docker exec tour-redis redis-cli dbsize) keys left)"
    ;;
  down)
    [[ -f "$PIDS" ]] && xargs kill < "$PIDS" 2>/dev/null || true
    pkill -f "uvicorn --factory tier0.api.app:create_app_from_env --port 810" 2>/dev/null || true
    rm -f "$PIDS"; docker rm -f tour-redis >/dev/null 2>&1 || true
    echo "stopped"
    ;;
esac
