#!/usr/bin/env bash
#
# An off-provider logical backup of the Railway database, and a restore drill.
#
#   scripts/backup_database.sh dump                take and checksum a dump
#   scripts/backup_database.sh drill <dump>        restore it and verify
#   scripts/backup_database.sh prune               apply the retention policy
#
# Railway's own volume backups are the first line and are not this. Deleting a
# Railway volume deletes its volume backups with it, so the disaster-recovery
# copy has to live somewhere else entirely — which is what this produces.
#
# The dump is PostgreSQL custom format, which is what `pg_restore` needs to
# rebuild selectively and in parallel. It must be taken with client tools
# matching the *server's* major version: the server is 18 and a pg_dump 17
# refuses it outright. That mismatch is exactly what made a recovery look like
# a corrupt archive on 2026-09-02, so the version is checked rather than
# assumed.
#
# Retention: seven daily, four weekly. Nothing here deletes the migration
# archive under artifacts/migration/, which has its own explicit decision.

set -euo pipefail

PG18="${PG18:-/opt/homebrew/opt/postgresql@18/bin}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT/artifacts/db-backups}"
DAILY_KEEP="${DAILY_KEEP:-7}"
WEEKLY_KEEP="${WEEKLY_KEEP:-4}"

die() { echo "error: $*" >&2; exit 1; }

# The URL arrives by environment and is never echoed. Every message about it
# says only what class of host it is.
require_url() {
    [ -n "${BACKUP_DATABASE_URL:-}" ] || die "BACKUP_DATABASE_URL is not set"
    case "$BACKUP_DATABASE_URL" in
        *rlwy.net*|*railway.internal*) echo "target: railway" ;;
        *supabase*) die "refusing to back up Supabase through this script" ;;
        *127.0.0.1*|*localhost*) echo "target: local" ;;
        *) echo "target: external" ;;
    esac
}

check_versions() {
    local server client
    server="$("$PG18/psql" "$BACKUP_DATABASE_URL" -Atc \
        "select current_setting('server_version_num')::int / 10000")"
    client="$("$PG18/pg_dump" --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')"
    [ "$server" = "$client" ] || die \
        "pg_dump is $client and the server is $server; a mismatched dump reads as a corrupt archive"
    echo "PostgreSQL $server, matching client tools"
}

do_dump() {
    require_url
    check_versions
    mkdir -p "$BACKUP_ROOT"
    local stamp target
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    target="$BACKUP_ROOT/railway-$stamp.dump"

    echo "dumping to $(basename "$target")"
    # --no-owner and --no-privileges: the restore target has its own roles, and
    # carrying app_runtime grants into a scratch database only produces errors.
    #
    # --compress=1 and keepalives, both learned here. At compress=9 pg_dump
    # spends long stretches of CPU on zlib between reads, the socket to
    # Railway's TCP proxy goes idle, and the proxy drops it — which surfaces
    # as "server closed the connection unexpectedly" partway through
    # chunk_embeddings while the server itself is demonstrably fine and still
    # checkpointing. Light compression keeps the socket busy, and the
    # keepalives stop an idle stretch being read as a dead peer.
    "$PG18/pg_dump" "$BACKUP_DATABASE_URL?keepalives=1&keepalives_idle=10&keepalives_interval=5&keepalives_count=6" \
        --format=custom --compress=1 --no-owner --no-privileges \
        --schema=public --schema=video --schema=auth --schema=supabase_migrations \
        --file="$target"

    ( cd "$BACKUP_ROOT" && shasum -a 256 "$(basename "$target")" > "$(basename "$target").sha256" )
    echo "wrote $(du -h "$target" | cut -f1) and its sha256"

    # A dump nobody can list the contents of is not yet evidence of anything.
    local tables
    tables="$("$PG18/pg_restore" -l "$target" | grep -c 'TABLE DATA' || true)"
    echo "archive lists $tables tables with data"
    [ "$tables" -ge 50 ] || die "archive lists only $tables tables; expected at least 50"
    echo "$target"
}

do_drill() {
    local dump="${1:-}"
    [ -n "$dump" ] || dump="$(ls -t "$BACKUP_ROOT"/railway-*.dump 2>/dev/null | head -1)"
    [ -f "$dump" ] || die "no dump to drill; run 'dump' first"
    [ -n "${DRILL_DATABASE_URL:-}" ] || die "DRILL_DATABASE_URL is not set (a scratch database)"
    case "$DRILL_DATABASE_URL" in
        *rlwy.net*|*railway.internal*) die "refusing to drill into Railway; use a scratch database" ;;
    esac

    echo "verifying checksum"
    ( cd "$(dirname "$dump")" && shasum -a 256 -c "$(basename "$dump").sha256" )

    # Prepare the target the way the archive expects to find it. The dump
    # creates `public` itself, so it must not already be there; and it declares
    # every vector column as `extensions.halfvec` without carrying the
    # `extensions` schema or the extension, because the source's platform
    # provided them. Restoring without these is the 2026-09-02 failure exactly:
    # the vector columns fail, twenty-six errors cascade, and what is left
    # looks restored and has lost every embedding.
    echo "preparing the scratch database"
    "$PG18/psql" "$DRILL_DATABASE_URL" -q -v ON_ERROR_STOP=1 \
        -c "drop schema if exists public cascade" \
        -c "drop schema if exists video cascade" \
        -c "drop schema if exists auth cascade" \
        -c "drop schema if exists supabase_migrations cascade" \
        -c "create schema if not exists extensions" \
        -c "create extension if not exists vector with schema extensions" \
        -c "create extension if not exists pgcrypto with schema extensions"

    echo "restoring $(basename "$dump") into the scratch database"
    # --exit-on-error is the whole point. A restore with warnings is a failed
    # restore: on 2026-09-02 one that skipped every vector column reported
    # success and had lost every embedding.
    "$PG18/pg_restore" --dbname="$DRILL_DATABASE_URL" \
        --no-owner --no-privileges --exit-on-error --single-transaction "$dump"

    echo "verifying the restored copy"
    "$PG18/psql" "$DRILL_DATABASE_URL" -Atc "analyze" >/dev/null
    "$PG18/psql" "$DRILL_DATABASE_URL" -At -F'|' <<'SQL'
select 'migration_head', max(version) from supabase_migrations.schema_migrations;
select 'books', count(*) from public.books;
select 'content_blocks', count(*) from public.content_blocks;
select 'chunk_embeddings', count(*)::text || ' at ' || string_agg(distinct dimension::text, '/')
  from public.chunk_embeddings;
select 'evidence_embeddings', count(*)::text || ' at ' || string_agg(distinct dimension::text, '/')
  from video.evidence_embeddings;
select 'image_blocks_without_key', count(*) from public.image_blocks where storage_key is null;
select 'identities', count(*) from auth.users;
select 'embedding_type', distinct_type from (
  select distinct pg_typeof(embedding)::text as distinct_type from public.chunk_embeddings
) s;
SQL
    echo "drill complete"
}

do_prune() {
    mkdir -p "$BACKUP_ROOT"
    local -a dumps
    # shellcheck disable=SC2207
    dumps=($(ls -t "$BACKUP_ROOT"/railway-*.dump 2>/dev/null || true))
    local kept=0 removed=0 index=0
    for dump in "${dumps[@]}"; do
        index=$((index + 1))
        if [ "$index" -le "$DAILY_KEEP" ]; then
            kept=$((kept + 1)); continue
        fi
        # Keep one per ISO week beyond the daily window, up to WEEKLY_KEEP.
        local week
        week="$(date -u -r "$dump" +%G-W%V)"
        if [ ! -f "$BACKUP_ROOT/.week-$week" ] && [ "$(ls "$BACKUP_ROOT"/.week-* 2>/dev/null | wc -l)" -lt "$WEEKLY_KEEP" ]; then
            touch "$BACKUP_ROOT/.week-$week"; kept=$((kept + 1)); continue
        fi
        rm -f "$dump" "$dump.sha256"; removed=$((removed + 1))
    done
    echo "kept $kept dumps, removed $removed"
}

case "${1:-}" in
    dump) do_dump ;;
    drill) shift; do_drill "${1:-}" ;;
    prune) do_prune ;;
    *) die "usage: scripts/backup_database.sh <dump|drill [file]|prune>" ;;
esac
