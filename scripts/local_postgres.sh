#!/usr/bin/env bash
#
# A development database without Docker.
#
#   scripts/local_postgres.sh start     create the cluster, apply migrations
#   scripts/local_postgres.sh stop
#   scripts/local_postgres.sh reset     throw it away and rebuild
#   scripts/local_postgres.sh status
#
# `supabase start` is the supported path and this is not a replacement for it:
# it has no auth service, no storage API, and no PostgREST. What it has is the
# schema — every migration applied to a real Postgres 17 — which is what the
# storage and ingestion tests actually talk to, and what `supabase start`
# cannot provide on a machine where Docker will not run.
#
# Requires: postgresql@17 and pgvector.
#
#   brew install postgresql@17 pgvector
#
# pgvector installs its files under its own prefix rather than into the server
# it was built for, so this links them in on first run.

set -euo pipefail

PG_PREFIX="${PG_PREFIX:-/opt/homebrew/opt/postgresql@17}"
PGVECTOR_PREFIX="${PGVECTOR_PREFIX:-/opt/homebrew/opt/pgvector}"
BIN="$PG_PREFIX/bin"
PORT="${PGPORT:-54322}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA="${PGDATA:-$ROOT/.local-postgres}"
PSQL=("$BIN/psql" -h 127.0.0.1 -p "$PORT" -U postgres -d postgres -v ON_ERROR_STOP=1 -q)

# initdb refuses a locale it cannot resolve, which is the default on a Mac
# whose LANG names a UTF-8 locale the C library does not carry.
export LC_ALL=C

die() { echo "error: $*" >&2; exit 1; }

link_pgvector() {
    local share lib
    share="$("$BIN/pg_config" --sharedir)/extension"
    lib="$("$BIN/pg_config" --pkglibdir)"
    [ -f "$share/vector.control" ] && return 0
    [ -d "$PGVECTOR_PREFIX" ] || die "pgvector not installed: brew install pgvector"
    mkdir -p "$share" "$lib"
    ln -sf "$PGVECTOR_PREFIX"/share/postgresql@17/extension/* "$share/"
    ln -sf "$PGVECTOR_PREFIX"/lib/postgresql@17/vector.dylib "$lib/vector.dylib"
    echo "linked pgvector into $PG_PREFIX"
}

running() { "$BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; }

start() {
    [ -x "$BIN/initdb" ] || die "postgresql@17 not installed: brew install postgresql@17"
    link_pgvector
    if [ ! -d "$PGDATA/base" ]; then
        "$BIN/initdb" -D "$PGDATA" -U postgres --auth=trust --locale=C --encoding=UTF8 >/dev/null
        echo "created cluster at $PGDATA"
    fi
    if running; then
        echo "already running on port $PORT"
    else
        "$BIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" -w \
            -o "-p $PORT -k /tmp -c listen_addresses=127.0.0.1" start >/dev/null
        echo "started on port $PORT"
    fi
    # The shim is idempotent; the migrations are not, so applying them twice
    # fails on the first `add column`. Which have run is therefore recorded, in
    # the table and the shape Supabase itself uses, so `start` against an
    # existing cluster applies only what is new.
    "${PSQL[@]}" -f "$ROOT/supabase/local/supabase_shim.sql"
    "${PSQL[@]}" -c "create schema if not exists supabase_migrations" >/dev/null
    "${PSQL[@]}" -c "create table if not exists supabase_migrations.schema_migrations (
                         version text primary key,
                         inserted_at timestamptz not null default now()
                     )" >/dev/null
    local applied=0 skipped=0 version
    for migration in "$ROOT"/supabase/migrations/*.sql; do
        version="$(basename "$migration" .sql)"
        version="${version%%_*}"
        if [ -n "$("${PSQL[@]}" -tAc "select 1 from supabase_migrations.schema_migrations where version = '$version'")" ]; then
            skipped=$((skipped + 1))
            continue
        fi
        "${PSQL[@]}" -f "$migration" >/dev/null
        "${PSQL[@]}" -c "insert into supabase_migrations.schema_migrations (version) values ('$version')" >/dev/null
        applied=$((applied + 1))
    done
    echo "applied $applied migrations ($skipped already applied)"
    echo "DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:$PORT/postgres"
}

case "${1:-start}" in
    start) start ;;
    stop) running && "$BIN/pg_ctl" -D "$PGDATA" -w stop >/dev/null && echo stopped || echo "not running" ;;
    reset)
        running && "$BIN/pg_ctl" -D "$PGDATA" -w stop >/dev/null || true
        rm -rf "$PGDATA"
        echo "cluster removed"
        start
        ;;
    status) running && echo "running on port $PORT" || echo "not running" ;;
    *) die "unknown command: $1" ;;
esac
