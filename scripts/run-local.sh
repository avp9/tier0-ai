#!/usr/bin/env bash
# Start the whole Tier0 app (web app, API, PostgreSQL, Redis) in Docker on this machine.
#
#   scripts/run-local.sh            start it (builds on the first run)
#   scripts/run-local.sh logs       follow the app's log
#   scripts/run-local.sh status     show the containers
#   scripts/run-local.sh down       stop it (data is kept)
#   scripts/run-local.sh reset      stop it and delete all its data (database, index, secrets)
#
# Options for start:  --port N  --bind ADDRESS  --yes (no questions; also agrees to replace an
#   unreadable database left by a run in another folder)  --random-demo-passwords
#
# Needs: Docker (Docker Desktop on a Mac). Keys come from .env; any that are missing it asks for
# (typing is hidden) and saves them in .env only: OPENAI_API_KEY (required), TYPESAFE_API_KEY,
# JIRA_API_TOKEN (then also the Jira site and user) and LANGSMITH_API_KEY (all optional: press
# Enter to skip). Without Jira, tickets go to an in-memory stand-in (lost when the app restarts);
# without a LangSmith key nothing is traced. Works the same on a Mac and on a Linux server.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RUN_DIR=".run"
COMPOSE_FILE="docker-compose.run.yml"

COMMAND="up"; PORT=8000; BIND="127.0.0.1"; ASSUME_YES=0; RANDOM_PASSWORDS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    up|down|logs|status|reset) COMMAND="$1" ;;
    --port) PORT="$2"; shift ;;
    --bind) BIND="$2"; shift ;;
    --yes|-y) ASSUME_YES=1 ;;
    --random-demo-passwords) RANDOM_PASSWORDS=1 ;;
    -h|--help) sed -n '2,/^set -euo/{/^set -euo/!p;}' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
ask() { # ask "question" -> true if yes
  [[ "$ASSUME_YES" == 1 ]] && return 0
  [[ -t 0 ]] || die "$1 (run again with --yes to agree)"
  local reply; read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[Yy]$ ]]
}
random_text() { # random_text LENGTH -> letters and digits
  LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1" || true
}
dc() { docker compose -p tier0 -f "$COMPOSE_FILE" --env-file "$RUN_DIR/compose.env" "$@"; }

check_docker() {
  command -v docker >/dev/null || die "Docker is not installed. Install Docker Desktop (Mac) or Docker Engine (Linux): https://docs.docker.com/get-docker/"
  docker info >/dev/null 2>&1 || die "Docker is installed but not running. Start Docker Desktop and try again."
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is missing (update Docker Desktop, or install the docker-compose-plugin)."
}

env_value() { # env_value KEY -> the value in .env (quotes removed), empty if none
  [[ -f .env ]] || return 0
  local line; line="$(grep -E "^$1=" .env | tail -n 1 || true)"
  line="${line#*=}"; line="${line%\"}"; line="${line#\"}"; line="${line%\'}"; line="${line#\'}"
  printf '%s' "$line"
}

set_env() { # set_env KEY VALUE -> put KEY=VALUE in .env (replace the line, or add it)
  [[ -f .env ]] || { cp .env.example .env 2>/dev/null || : > .env; }
  local tmp; tmp="$(mktemp)"
  K="$1" V="$2" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; done=0}
    index($0, k "=")==1 {print k "=" v; done=1; next} {print}
    END{if(!done) print k "=" v}' .env > "$tmp"
  cat "$tmp" > .env; rm -f "$tmp"; chmod 600 .env
}

prompt_secret() { # prompt_secret KEY "what it is for" required|optional -> asks if .env has no value
  local key="$1" purpose="$2" need="$3" value
  [[ -n "$(env_value "$key")" ]] && return 0
  if [[ ! -t 0 ]]; then
    [[ "$need" == required ]] && die "$key is not set. Copy .env.example to .env and put it in."
    return 0
  fi
  if [[ "$need" == required ]]; then
    read -r -s -p "$key ($purpose): " value; echo
    [[ -n "$value" ]] || die "$key is required."
  else
    read -r -s -p "$key ($purpose; press Enter to skip): " value; echo
    [[ -n "$value" ]] || return 0
  fi
  set_env "$key" "$value"
}

prompt_text() { # prompt_text KEY "question" -> asks (visible) if .env has no value
  local key="$1" value
  [[ -n "$(env_value "$key")" ]] && return 0
  read -r -p "$2: " value
  [[ -n "$value" ]] && set_env "$key" "$value"
  return 0
}

ensure_dotenv_key() {
  local first=0; [[ -f .env ]] || first=1
  if [[ "$first" == 1 && -t 0 ]]; then
    say "No .env yet. I will ask for your keys and save them only in .env on this machine."
  fi
  prompt_secret OPENAI_API_KEY "needed: answers and search" required
  [[ "$(env_value OPENAI_API_KEY)" == sk-* ]] || die "OPENAI_API_KEY does not look like an OpenAI key (it should start with sk-)."
  prompt_secret TYPESAFE_API_KEY "optional: intent classification with JEV; OpenAI is used without it" optional
  prompt_secret JIRA_API_TOKEN "optional: create real Jira tickets; a stand-in is used without it" optional
  if [[ -n "$(env_value JIRA_API_TOKEN)" ]]; then
    [[ -t 0 ]] && prompt_text JIRA_SERVER "Jira site URL (for example https://yourcompany.atlassian.net)"
    [[ -t 0 ]] && prompt_text JIRA_USERNAME "Jira user email the token belongs to"
  fi
  prompt_secret LANGSMITH_API_KEY "optional: send traces and feedback to LangSmith" optional
  if [[ -n "$(env_value LANGSMITH_API_KEY)" && "$(env_value LANGSMITH_TRACING)" != "true" ]]; then
    set_env LANGSMITH_TRACING true
  fi
}

# Secrets this script makes up (kept in .run/, never in .env, never in git).
load_generated() {
  mkdir -p "$RUN_DIR"; chmod 700 "$RUN_DIR"
  local f="$RUN_DIR/secrets.env"
  FRESH_SECRETS=0; [[ -s "$f" ]] || FRESH_SECRETS=1  # first run in this folder
  touch "$f"; chmod 600 "$f"
  add_secret() { grep -q "^$1=" "$f" || printf '%s=%s\n' "$1" "$2" >> "$f"; }
  add_secret TIER0_DB_PASSWORD "$(random_text 32)"
  add_secret GENERATED_SESSION_SECRET "$(random_text 64)"
  if [[ "$RANDOM_PASSWORDS" == 1 ]]; then
    add_secret ALICE_PASSWORD "$(random_text 16)"; add_secret BOB_PASSWORD "$(random_text 16)"
  else
    add_secret ALICE_PASSWORD "alice-demo-pass-1"; add_secret BOB_PASSWORD "bob-demo-pass-22"
  fi
  # shellcheck disable=SC1090
  set -a; . "$f"; set +a
}

write_env_files() {
  local ticketing="fake"
  if [[ -n "$(env_value JIRA_SERVER)" && -n "$(env_value JIRA_USERNAME)" && -n "$(env_value JIRA_API_TOKEN)" ]]; then
    ticketing="jira"
  fi
  TICKETING="$ticketing"
  local session users
  session="$(env_value SESSION_SECRET)"; [[ ${#session} -ge 32 ]] || session="$GENERATED_SESSION_SECRET"
  users="$(env_value DEMO_USERS)"
  USERS_FROM_ENV=1
  if [[ -z "$users" ]]; then
    USERS_FROM_ENV=0
    users="[{\"email\":\"alice@buzztech.example\",\"name\":\"Alice Demo\",\"password\":\"$ALICE_PASSWORD\",\"gender\":\"female\"},{\"email\":\"bob@buzztech.example\",\"name\":\"Bob Demo\",\"password\":\"$BOB_PASSWORD\",\"gender\":\"male\"}]"
  fi
  local app="$RUN_DIR/app.env"
  : > "$app"; chmod 600 "$app"
  # Everything from .env except empty values and the ones this script decides itself.
  if [[ -f .env ]]; then
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=.+' .env \
      | grep -Ev '^(DATABASE_URL|REDIS_URL|VECTOR_STORE_PATH|STATIC_DIR|APP_ENV|LLM_PROVIDER|TICKETING_PROVIDER|SESSION_SECRET|DEMO_USERS|E2E_TOKEN|TIER0_TEST_[A-Z_]*|LANGSMITH_AGENT_ENVIRONMENT)=' \
      >> "$app" || true
  fi
  {
    echo "APP_ENV=local"
    echo "LANGSMITH_AGENT_ENVIRONMENT=local"
    echo "LLM_PROVIDER=openai"
    echo "TICKETING_PROVIDER=$ticketing"
    echo "SESSION_SECRET=$session"
    echo "DEMO_USERS='$users'"
  } >> "$app"
  {
    echo "TIER0_DB_PASSWORD=$TIER0_DB_PASSWORD"
    echo "TIER0_BIND=$BIND"
    echo "TIER0_PORT=$PORT"
  } > "$RUN_DIR/compose.env"
  chmod 600 "$RUN_DIR/compose.env"
}

port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

index_present() {
  dc --profile tools run --rm --no-deps --entrypoint sh indexer -c 'test -f /app/data/chroma/chroma.sqlite3 && test -f /app/data/chroma/sparse/meta.json' >/dev/null 2>&1
}

prepare_index() {
  if ! index_present; then
    if [[ -f .data/chroma/chroma.sqlite3 && -f .data/chroma/sparse/meta.json ]]; then
      say "Copying the prebuilt knowledge-base index (.data/chroma) into Docker..."
      dc --profile tools run --rm --no-deps --user root -v "$ROOT/.data/chroma:/seed:ro" \
        --entrypoint sh indexer -c 'cp -a /seed/. /app/data/chroma/ && chown -R 10001 /app/data/chroma'
    else
      say "No prebuilt knowledge-base index found. Building one for ~21,000 articles:"
      say "  - about 15 to 40 minutes (the search model runs on your CPU) and a few cents of OpenAI embeddings"
      say "  - done once; later starts skip it"
      ask "Build it now?" || die "Stopped. Get the prebuilt .data/chroma folder, or run again and agree."
    fi
  fi
  say "Checking the knowledge-base index..."
  dc --profile tools run --rm --no-deps indexer
}

cmd_up() {
  check_docker
  [[ -f "$COMPOSE_FILE" ]] || die "Run this from a Tier0 checkout ($COMPOSE_FILE not found)."
  ensure_dotenv_key
  load_generated
  write_env_files
  if port_in_use "$PORT" && [[ -z "$(dc ps -q app 2>/dev/null)" ]]; then
    die "Port $PORT is already in use. Choose another: scripts/run-local.sh --port 8001"
  fi
  if [[ "$FRESH_SECRETS" == 1 ]] && docker volume inspect tier0_pgdata >/dev/null 2>&1; then
    # A database from a run in another folder: its password is not here, so it cannot be opened.
    say "A Tier0 database from an earlier run exists, but this folder does not have its password."
    ask "Delete that old database (demo users and chats only) and start fresh?" \
      || die "Stopped. Run it from the folder you used before, or agree to replace the database."
    dc down >/dev/null 2>&1 || true
    docker volume rm tier0_pgdata >/dev/null
  fi
  say "Building the app (the first time takes several minutes: it downloads the two search models)..."
  dc build app
  prepare_index
  say "Starting the app, PostgreSQL and Redis..."
  dc up -d --wait --no-build
  local host="localhost"; [[ "$BIND" == "127.0.0.1" ]] || host="$BIND"
  say ""
  say "Tier0 is running:  http://$host:$PORT"
  if [[ "$USERS_FROM_ENV" == 1 ]]; then
    say "Sign in with an account from DEMO_USERS in .env."
  else
    say "Sign in with:  alice@buzztech.example / $ALICE_PASSWORD   (or bob@buzztech.example / $BOB_PASSWORD)"
  fi
  if [[ "$TICKETING" == "fake" ]]; then
    say "Tickets: in-memory stand-in (no Jira settings in .env); they disappear when the app restarts."
  else
    say "Tickets: created in Jira ($(env_value JIRA_SERVER))."
  fi
  say "Stop:  scripts/run-local.sh down      Logs:  scripts/run-local.sh logs"
}

case "$COMMAND" in
  up) cmd_up ;;
  logs) check_docker; [[ -f "$RUN_DIR/compose.env" ]] || die "Not started yet."; dc logs -f --tail 100 app ;;
  status) check_docker; [[ -f "$RUN_DIR/compose.env" ]] || die "Not started yet."; dc ps ;;
  down) check_docker; [[ -f "$RUN_DIR/compose.env" ]] || die "Not started yet."; dc down; say "Stopped. Data is kept; start again with scripts/run-local.sh" ;;
  reset)
    check_docker
    ask "Delete the database, the index volume and the generated secrets?" || exit 1
    if [[ -f "$RUN_DIR/compose.env" ]]; then dc --profile tools down -v; fi
    rm -rf "$RUN_DIR"; say "Everything deleted (your .env and .data/chroma are untouched)."
    ;;
esac
