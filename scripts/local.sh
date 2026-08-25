#!/usr/bin/env bash
# Reproducible full local environment: Supabase Auth/Storage/Postgres plus the
# combined API/worker and web app. No command here points at hosted services.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPABASE_VERSION="${SUPABASE_VERSION:-2.109.1}"
SUPABASE=(npx --yes "supabase@${SUPABASE_VERSION}")
# These services are not used by the app. Keeping them out saves about 5 GB
# locally while retaining the complete runtime contract: database, Auth,
# Storage, REST, Realtime, gateway, metadata, and local email capture.
SUPABASE_EXCLUDES="studio,logflare,edge-runtime,vector,imgproxy"
COMPOSE=(docker compose --env-file "$ROOT/.env" -f "$ROOT/docker-compose.yml")
RUNTIME_DIR="$ROOT/.local-runtime"

die() { echo "error: $*" >&2; exit 1; }
note() { echo "local: $*"; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

docker_ready() {
    docker info >/dev/null 2>&1
}

require_docker() {
    require docker
    docker_ready || die "Docker is not running. Start Docker Desktop or Colima, then retry."
    docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
}

set_env() {
    local file="$1" key="$2" value="$3" temporary
    temporary="$(mktemp)"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        $0 ~ ("^" key "=") { print key "=" value; found = 1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "$file" >"$temporary"
    mv "$temporary" "$file"
}

status_value() {
    local status="$1" key="$2"
    printf '%s\n' "$status" \
        | sed -n -E "s/^${key}=\"(.*)\"$/\\1/p" \
        | tail -n 1
}

sync_env() {
    local status api_url db_url anon_key service_key jwt_secret
    status="$(cd "$ROOT" && "${SUPABASE[@]}" status -o env)"
    api_url="$(status_value "$status" API_URL)"
    db_url="$(status_value "$status" DB_URL)"
    anon_key="$(status_value "$status" ANON_KEY)"
    service_key="$(status_value "$status" SERVICE_ROLE_KEY)"
    jwt_secret="$(status_value "$status" JWT_SECRET)"
    [[ -n "$api_url" && -n "$db_url" && -n "$anon_key" \
       && -n "$service_key" && -n "$jwt_secret" ]] \
        || die "Supabase did not report all required local credentials"

    [[ -f "$ROOT/.env" ]] || cp "$ROOT/.env.example" "$ROOT/.env"
    [[ -f "$ROOT/frontend/.env.local" ]] \
        || cp "$ROOT/frontend/.env.example" "$ROOT/frontend/.env.local"
    chmod 600 "$ROOT/.env" "$ROOT/frontend/.env.local"

    set_env "$ROOT/.env" DATABASE_URL "$db_url"
    set_env "$ROOT/.env" MIGRATION_DATABASE_URL "$db_url"
    set_env "$ROOT/.env" DOCKER_DATABASE_URL \
        "postgresql://postgres:postgres@host.docker.internal:54322/postgres"
    set_env "$ROOT/.env" SUPABASE_URL "$api_url"
    set_env "$ROOT/.env" DOCKER_SUPABASE_URL \
        "http://host.docker.internal:54321"
    set_env "$ROOT/.env" SUPABASE_SERVICE_ROLE_KEY "$service_key"
    set_env "$ROOT/.env" SUPABASE_JWT_SECRET "$jwt_secret"
    set_env "$ROOT/.env" SUPABASE_JWT_ISSUER \
        "http://127.0.0.1:54321/auth/v1"
    set_env "$ROOT/.env" CORS_ALLOWED_ORIGINS "http://localhost:3000"
    set_env "$ROOT/.env" NEXT_PUBLIC_SUPABASE_URL "$api_url"
    set_env "$ROOT/.env" NEXT_PUBLIC_SUPABASE_ANON_KEY "$anon_key"
    set_env "$ROOT/.env" NEXT_PUBLIC_API_ORIGIN "http://127.0.0.1:8000"

    set_env "$ROOT/frontend/.env.local" NEXT_PUBLIC_SUPABASE_URL "$api_url"
    set_env "$ROOT/frontend/.env.local" NEXT_PUBLIC_SUPABASE_ANON_KEY "$anon_key"
    set_env "$ROOT/frontend/.env.local" NEXT_PUBLIC_API_ORIGIN \
        "http://127.0.0.1:8000"
    note "synchronized local endpoints and credentials (values not printed)"
}

stop_plain_postgres() {
    if "$ROOT/scripts/local_postgres.sh" status 2>/dev/null | grep -q '^running'; then
        note "stopping the schema-only Postgres fallback on Supabase's port"
        "$ROOT/scripts/local_postgres.sh" stop
    fi
}

start_supabase() {
    require_docker
    require npx
    stop_plain_postgres
    # `supabase start` prints all local credentials when the stack is already
    # running. Keep them out of terminal logs; `sync_env` consumes them safely.
    if ! (cd "$ROOT" && "${SUPABASE[@]}" start \
        --exclude "$SUPABASE_EXCLUDES" >/dev/null); then
        die "Supabase failed to start; rerun its command with --debug for details"
    fi
    docker exec -i supabase_db_agentic_study_partner \
        psql -U postgres -d postgres -v ON_ERROR_STOP=1 \
        < "$ROOT/supabase/deferred/book_sources_storage.sql" >/dev/null
    sync_env
}

setup() {
    require uv
    require npm
    start_supabase
    (cd "$ROOT" && uv sync --frozen)
    (cd "$ROOT/frontend" && npm ci)
    note "setup complete; run scripts/local.sh up"
}

up() {
    start_supabase
    mkdir -p "$RUNTIME_DIR"
    start_process app "$ROOT" uv run python -m scripts.serve
    start_process web "$ROOT/frontend" npm run dev
    wait_for_url http://127.0.0.1:8000/api/health api
    wait_for_url http://127.0.0.1:3000 web
    note "web: http://localhost:3000"
    note "api: http://localhost:8000/api/health"
    note "local email inbox: http://127.0.0.1:54324"
}

start_process() {
    local name="$1" directory="$2" pid_file="$RUNTIME_DIR/$1.pid"
    shift 2
    if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
        note "${name} already running (pid $(<"$pid_file"))"
        return
    fi
    (
        cd "$directory"
        nohup "$@" >"$RUNTIME_DIR/${name}.log" 2>&1 &
        echo "$!" >"$pid_file"
    )
    note "started ${name} (pid $(<"$pid_file"))"
}

wait_for_url() {
    local url="$1" name="$2"
    for _ in $(seq 1 60); do
        if curl --fail --silent --show-error --output /dev/null "$url"; then
            return
        fi
        sleep 2
    done
    tail -n 40 "$RUNTIME_DIR/${name}.log" >&2 || true
    die "${name} did not become ready"
}

stop_process() {
    local name="$1" pid_file="$RUNTIME_DIR/$1.pid" pid
    [[ -f "$pid_file" ]] || return
    pid="$(<"$pid_file")"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid"
        for _ in $(seq 1 20); do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
    fi
    : >"$pid_file"
}

down() {
    stop_process web
    stop_process app
    (cd "$ROOT" && "${SUPABASE[@]}" stop)
}

up_containers() {
    start_supabase
    "${COMPOSE[@]}" up --build --detach
    note "containerized stack started"
}

down_containers() {
    "${COMPOSE[@]}" down
    (cd "$ROOT" && "${SUPABASE[@]}" stop)
}

reset() {
    start_supabase
    (cd "$ROOT" && "${SUPABASE[@]}" db reset --local --yes)
    sync_env
    note "local database reset; container media volume was preserved"
}

doctor() {
    require_docker
    (cd "$ROOT" && "${SUPABASE[@]}" status >/dev/null)
    [[ -f "$ROOT/.env" && -f "$ROOT/frontend/.env.local" ]] \
        || die "local env files are missing; run scripts/local.sh setup"
    for name in app web; do
        [[ -s "$RUNTIME_DIR/${name}.pid" ]] \
            && kill -0 "$(<"$RUNTIME_DIR/${name}.pid")" 2>/dev/null \
            || die "${name} process is not running"
    done
    curl --fail --silent --show-error http://127.0.0.1:8000/api/health \
        | grep -q '"status":"ok"' \
        || die "API health check failed"
    curl --fail --silent --show-error --output /dev/null http://127.0.0.1:3000 \
        || die "web health check failed"
    for path in auth/v1/health rest/v1/ storage/v1/status; do
        curl --fail --silent --show-error --output /dev/null \
            "http://127.0.0.1:54321/${path}" \
            || die "Supabase ${path} health check failed"
    done
    note "Supabase Auth/REST/Storage, supervised API/worker, and web are reachable"
}

logs() {
    local name="${1:-app}"
    [[ "$name" == "app" || "$name" == "web" ]] \
        || die "logs service must be app or web"
    tail -n 100 -f "$RUNTIME_DIR/${name}.log"
}

case "${1:-help}" in
    setup) setup ;;
    up|start) up ;;
    down|stop) down ;;
    up-containers) up_containers ;;
    down-containers) down_containers ;;
    reset) reset ;;
    doctor) doctor ;;
    logs) logs "${2:-}" ;;
    *)
        cat <<'EOF'
usage: scripts/local.sh <setup|up|down|reset|doctor|logs [app|web]>

  setup   start Supabase, synchronize ignored env files, install dependencies
  up      start API/worker and web natively against local Supabase
  down    stop the app and Supabase without deleting their data
  reset   rebuild only the local Supabase database from migrations and seed
  doctor  verify Supabase, supervised API/worker, and web

Advanced: up-containers/down-containers run the app through Compose instead.
EOF
        ;;
esac
