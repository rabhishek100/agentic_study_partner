#!/usr/bin/env bash
# Build an isolated local snapshot of the production library.
#
# Production is read only throughout. Authentication secrets and sessions are
# never copied: the primary production owner receives a new local-only login.
# Database and video archives are streamed so the clone does not need a second
# copy of the corpus on a disk-constrained development machine.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT/.local-runtime"
PRODUCTION_ENVIRONMENT="${RAILWAY_PRODUCTION_ENVIRONMENT:-production}"
PRODUCTION_SERVICE="${RAILWAY_PRODUCTION_SERVICE:-api}"
LOCAL_EMAIL="${LOCAL_LIBRARY_EMAIL:-local-library@study-partner.test}"
KEYCHAIN_SERVICE="agentic-study-partner-local-library"
VIDEO_ROOT="$ROOT/data/video-media"
MINIMUM_FREE_KIB=$((3 * 1024 * 1024))
EXPECTED_PRODUCTION_SERVICE="api"

die() { echo "error: $*" >&2; exit 1; }
note() { echo "clone: $*"; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

usage() {
    cat <<'EOF'
usage: scripts/clone_prod_to_local.sh --yes

Replaces only the isolated local Supabase data, local Storage objects, and
data/video-media with a verified snapshot of the production library. The
production database, Storage bucket, and Railway volume are read only.

The local login is local-library@study-partner.test. Its generated password is
stored in macOS Keychain service agentic-study-partner-local-library.
EOF
}

[[ "${1:-}" == "--yes" && $# -eq 1 ]] || { usage; exit 2; }

for command in curl docker jq npx openssl psql python3 railway security shasum tar; do
    require "$command"
done

[[ "$PRODUCTION_ENVIRONMENT" == "production" ]] \
    || die "the source Railway environment must be exactly production"
[[ "$PRODUCTION_SERVICE" == "$EXPECTED_PRODUCTION_SERVICE" ]] \
    || die "the source Railway service must be exactly api"
[[ -d "$ROOT/supabase/migrations" ]] || die "run this command from the repository"

free_kib="$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')"
[[ "$free_kib" =~ ^[0-9]+$ ]] || die "could not determine free disk space"
(( free_kib >= MINIMUM_FREE_KIB )) \
    || die "at least 3 GiB free is required for the streaming clone"

note "loading production connection settings without printing their values"
production_json="$(
    railway variables \
        --environment "$PRODUCTION_ENVIRONMENT" \
        --service "$PRODUCTION_SERVICE" \
        --json
)"
production_database_url="$(printf '%s' "$production_json" | jq -er '.DATABASE_URL')"
production_supabase_url="$(printf '%s' "$production_json" | jq -er '.SUPABASE_URL')"
production_service_key="$(
    printf '%s' "$production_json" | jq -er '.SUPABASE_SERVICE_ROLE_KEY'
)"

[[ "$production_database_url" != *"127.0.0.1"* \
   && "$production_database_url" != *"localhost"* ]] \
    || die "production DATABASE_URL unexpectedly points at localhost"
[[ "$production_supabase_url" == https://* ]] \
    || die "production SUPABASE_URL must be hosted HTTPS"

production_connection_parts="$(
    CLONE_POOL_URL="$production_database_url" python3 - <<'PY'
import os
from urllib.parse import unquote, urlparse

pooled = urlparse(os.environ["CLONE_POOL_URL"])
print("\t".join((
    pooled.hostname or "",
    str(pooled.port or 5432),
    pooled.username or "",
    unquote(pooled.password or ""),
    (pooled.path or "/postgres").lstrip("/"),
)))
PY
)"
IFS=$'\t' read -r production_host production_port production_user \
    production_password production_database <<<"$production_connection_parts"
unset production_connection_parts
[[ -n "$production_host" && -n "$production_user" \
   && -n "$production_password" && -n "$production_database" ]] \
    || die "could not parse the production pooler connection"
prod_psql() {
    PGHOST="$production_host" \
    PGPORT="$production_port" \
    PGUSER="$production_user" \
    PGPASSWORD="$production_password" \
    PGDATABASE="$production_database" \
    command psql "$@"
}

primary_owner_id="$(
    prod_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select owner_id from books group by owner_id order by count(*) desc limit 1"
)"
[[ "$primary_owner_id" =~ ^[0-9a-f-]{36}$ ]] \
    || die "could not resolve the primary production library owner"

expected_document_count="$(
    prod_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select count(*) from books
            where owner_id = '$primary_owner_id'::uuid and status = 'ready'"
)"
expected_video_count="$(
    prod_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select count(*) from video.videos as source_video
            where owner_id = '$primary_owner_id'::uuid
              and not exists (
                select 1 from video.course_lectures as lecture
                where lecture.owner_id = source_video.owner_id
                  and lecture.video_id = source_video.id
              )"
)"
[[ "$expected_document_count" =~ ^[0-9]+$ \
   && "$expected_video_count" =~ ^[0-9]+$ ]] \
    || die "could not determine the primary production library inventory"
(( expected_video_count <= 100 )) \
    || die "the authenticated video smoke test supports at most 100 standalone videos"

owner_contract="$(
    prod_psql -X -At -v ON_ERROR_STOP=1 -c "
        with owners as (
          select owner_id, count(*) as books from books group by owner_id
        )
        select count(*) = 2
           and bool_and(
                 owner_id = '00000000-0000-4000-8000-000000000001'::uuid
                 or owner_id = '$primary_owner_id'::uuid
               )
        from owners
    "
)"
[[ "$owner_contract" == "t" ]] \
    || die "production owners changed; review the local identity mapping first"

note "stopping local app processes and rebuilding only the local database"
"$ROOT/scripts/local.sh" down >/dev/null 2>&1 || true
"$ROOT/scripts/local.sh" reset
mkdir -p "$RUNTIME_DIR"

# local.sh reset refreshes these ignored values after Supabase restarts.
# shellcheck disable=SC1091
source "$ROOT/.env"
local_database_url="$DATABASE_URL"
local_supabase_url="$SUPABASE_URL"
local_service_key="$SUPABASE_SERVICE_ROLE_KEY"

[[ "$local_database_url" == *"127.0.0.1:54322"* ]] \
    || die "local DATABASE_URL is not the isolated Supabase CLI database"
[[ "$local_supabase_url" == "http://127.0.0.1:54321" ]] \
    || die "local SUPABASE_URL is not the isolated Supabase CLI gateway"

production_postgres_major="$(
    prod_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select current_setting('server_version_num')::integer / 10000"
)"
local_postgres_major="$(
    psql "$local_database_url" -X -At -v ON_ERROR_STOP=1 \
        -c "select current_setting('server_version_num')::integer / 10000"
)"
[[ "$local_postgres_major" == "$production_postgres_major" ]] \
    || die "production and local PostgreSQL major versions must match for binary copy"

local_password="$(openssl rand -hex 18)Aa1!"
create_status="$(
    curl --silent --show-error --output "$RUNTIME_DIR/local-user-response.json" \
        --write-out '%{http_code}' \
        --request POST "$local_supabase_url/auth/v1/admin/users" \
        --header "apikey: $local_service_key" \
        --header "Authorization: Bearer $local_service_key" \
        --header 'Content-Type: application/json' \
        --data "$(jq -cn \
            --arg id "$primary_owner_id" \
            --arg email "$LOCAL_EMAIL" \
            --arg password "$local_password" \
            '{id:$id,email:$email,password:$password,email_confirm:true}')"
)"
[[ "$create_status" == "200" ]] \
    || die "local Auth user creation failed with HTTP $create_status"
security add-generic-password -U \
    -s "$KEYCHAIN_SERVICE" -a "$LOCAL_EMAIL" -w "$local_password" >/dev/null
unset local_password

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
set_env "$ROOT/.env" DEFAULT_OWNER_ID "$primary_owner_id"

temporary_dir="$(mktemp -d "$RUNTIME_DIR/prod-clone.XXXXXX")"
cleanup() {
    [[ -d "${temporary_dir:-}" ]] \
        && find "$temporary_dir" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT

note "copying production public/video tables in bounded, resumable batches"
table_manifest="$(
    prod_psql -X -At -v ON_ERROR_STOP=1 -c "
      select schemaname || '.' || tablename
      from pg_tables
      where schemaname in ('public', 'video')
      order by schemaname, tablename
    "
)"
while IFS= read -r table_name; do
    [[ "$table_name" =~ ^(public|video)\.[a-z0-9_]+$ ]] \
        || die "unsafe production table name"
    row_count="$(
        prod_psql -X -At -v ON_ERROR_STOP=1 \
            -c "select count(*) from $table_name"
    )"
    columns="$(
        prod_psql -X -At -v ON_ERROR_STOP=1 -c "
          select string_agg(format('%I', attribute.attname), ', '
                            order by attribute.attnum)
          from pg_attribute attribute
          join pg_class relation on relation.oid = attribute.attrelid
          join pg_namespace namespace on namespace.oid = relation.relnamespace
          where namespace.nspname || '.' || relation.relname = '$table_name'
            and attribute.attnum > 0
            and not attribute.attisdropped
            and attribute.attgenerated = ''
        "
    )"
    [[ "$row_count" =~ ^[0-9]+$ && -n "$columns" ]] \
        || die "could not inspect $table_name"
    case "$table_name" in
        public.image_blocks) batch_size=50 ;;
        public.chunk_embeddings|video.evidence_embeddings) batch_size=1000 ;;
        *) batch_size=10000 ;;
    esac
    note "copying $table_name ($row_count rows)"
    psql "$local_database_url" -X -v ON_ERROR_STOP=1 -c \
        "set session_replication_role = replica; delete from $table_name;" \
        >/dev/null
    offset=0
    while (( offset < row_count )); do
        batch_file="$temporary_dir/table.copy"
        exported=false
        for attempt in 1 2 3; do
            if prod_psql -X -q -v ON_ERROR_STOP=1 \
                -c "copy (
                      select $columns from $table_name
                      order by ctid limit $batch_size offset $offset
                    ) to stdout with (format binary)" >"$batch_file"; then
                exported=true
                break
            fi
            note "$table_name batch at $offset failed (attempt $attempt of 3)"
        done
        [[ "$exported" == true ]] \
            || die "could not export $table_name batch at row $offset"
        {
            printf '%s\n' "set session_replication_role = replica;"
            printf "\\copy %s (%s) from '%s' with (format binary)\n" \
                "$table_name" "$columns" "$batch_file"
        } | psql "$local_database_url" -X -v ON_ERROR_STOP=1 >/dev/null
        : >"$batch_file"
        offset=$((offset + batch_size))
    done
done <<<"$table_manifest"

sequence_manifest="$(
    psql "$local_database_url" -X -At -F $'\t' -v ON_ERROR_STOP=1 -c "
      select namespace.nspname, relation.relname, attribute.attname
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      join pg_attribute attribute on attribute.attrelid = relation.oid
      where namespace.nspname in ('public', 'video')
        and relation.relkind = 'r'
        and attribute.attnum > 0
        and not attribute.attisdropped
        and pg_get_serial_sequence(
              format('%I.%I', namespace.nspname, relation.relname),
              attribute.attname
            ) is not null
      order by namespace.nspname, relation.relname, attribute.attnum
    "
)"
while IFS=$'\t' read -r sequence_schema sequence_table sequence_column; do
    [[ "$sequence_schema" =~ ^(public|video)$ \
       && "$sequence_table" =~ ^[a-z0-9_]+$ \
       && "$sequence_column" =~ ^[a-z0-9_]+$ ]] \
        || die "unsafe sequence owner in local schema"
    psql "$local_database_url" -X -v ON_ERROR_STOP=1 -c "
      select setval(
        pg_get_serial_sequence('$sequence_schema.$sequence_table', '$sequence_column'),
        coalesce(max($sequence_column), 1),
        max($sequence_column) is not null
      ) from $sequence_schema.$sequence_table
    " >/dev/null
done <<<"$sequence_manifest"

note "copying private book PDFs through the Storage APIs"
object_manifest="$temporary_dir/book-objects.tsv"
prod_psql -X -At -F $'\t' -v ON_ERROR_STOP=1 \
    -c "
      select object.name,
             coalesce(object.metadata->>'mimetype', 'application/pdf'),
             object.metadata->>'size',
             coalesce((
               select book.file_hash from books book
               where book.source_storage_bucket = object.bucket_id
                 and book.source_storage_path = object.name
               limit 1
             ), '')
      from storage.objects object
      where object.bucket_id = 'book-sources'
      order by object.name
    " >"$object_manifest"

copied_objects=0
copied_bytes=0
while IFS=$'\t' read -r object_path media_type expected_size expected_hash; do
    [[ "$object_path" =~ ^[0-9a-f-]{36}/[A-Za-z0-9._/-]+$ \
       && "$object_path" != *".."* ]] \
        || die "unsafe Storage object path in production manifest"
    object_file="$temporary_dir/object.pdf"
    curl --fail --silent --show-error --retry 3 \
        "$production_supabase_url/storage/v1/object/book-sources/$object_path" \
        --header "apikey: $production_service_key" \
        --header "Authorization: Bearer $production_service_key" \
        --output "$object_file"
    actual_size="$(wc -c <"$object_file" | tr -d ' ')"
    [[ "$actual_size" == "$expected_size" ]] \
        || die "Storage object size mismatch for $object_path"
    if [[ -n "$expected_hash" ]]; then
        actual_hash="$(shasum -a 256 "$object_file" | awk '{print $1}')"
        [[ "$actual_hash" == "$expected_hash" ]] \
            || die "Storage object hash mismatch for $object_path"
    fi
    curl --fail --silent --show-error --retry 3 \
        --request POST \
        "$local_supabase_url/storage/v1/object/book-sources/$object_path" \
        --header "apikey: $local_service_key" \
        --header "Authorization: Bearer $local_service_key" \
        --header 'x-upsert: true' \
        --header "Content-Type: $media_type" \
        --data-binary "@$object_file" >/dev/null
    copied_objects=$((copied_objects + 1))
    copied_bytes=$((copied_bytes + actual_size))
done <"$object_manifest"

note "copied $copied_objects book objects ($copied_bytes bytes)"

note "streaming the read-only Railway video volume into local media storage"
mkdir -p "$VIDEO_ROOT"
find "$VIDEO_ROOT" -mindepth 1 -depth -delete
remote_video_inventory="$(
    railway ssh \
        --environment "$PRODUCTION_ENVIRONMENT" \
        --service "$PRODUCTION_SERVICE" \
        sh -lc "find /var/lib/agentic-study-partner/video-media -type f \
          -exec stat -c %s {} \\; | awk \
          '{bytes += \$1; files += 1} END {print files \"|\" bytes}'"
)"
railway ssh \
    --environment "$PRODUCTION_ENVIRONMENT" \
    --service "$PRODUCTION_SERVICE" \
    tar -C /var/lib/agentic-study-partner/video-media -cf - . \
    | tar -C "$VIDEO_ROOT" -xf -
local_video_inventory="$(
    find "$VIDEO_ROOT" -type f -exec stat -f %z {} \; \
        | awk '{bytes += $1; files += 1} END {print files "|" bytes}'
)"
[[ "$local_video_inventory" == "$remote_video_inventory" ]] \
    || die "Railway video volume inventory did not match after transfer"

note "verifying every database-referenced video object by SHA-256"
video_manifest="$temporary_dir/video-objects.tsv"
psql "$local_database_url" -X -At -F $'\t' -v ON_ERROR_STOP=1 -c "
  select distinct storage_key, content_hash, coalesce(size_bytes::text, '')
  from (
    select storage_key, content_hash, size_bytes from video.video_sources
      where storage_key is not null
    union all
    select storage_key, content_hash, size_bytes from video.resources
      where storage_key is not null
    union all
    select full_storage_key, full_content_hash, null::bigint from video.frames
      where full_storage_key is not null
    union all
    select preview_storage_key, preview_content_hash, null::bigint from video.frames
      where preview_storage_key is not null
  ) objects
  order by storage_key
" >"$video_manifest"

verified_video_objects=0
while IFS=$'\t' read -r storage_key expected_hash expected_size; do
    [[ "$storage_key" =~ ^[0-9a-f-]{36}/[A-Za-z0-9._/-]+$ \
       && "$storage_key" != *".."* ]] \
        || die "unsafe video storage key in local manifest"
    media_file="$VIDEO_ROOT/$storage_key"
    [[ -f "$media_file" && ! -L "$media_file" ]] \
        || die "video media object is missing: $storage_key"
    if [[ -n "$expected_size" ]]; then
        actual_size="$(stat -f %z "$media_file")"
        [[ "$actual_size" == "$expected_size" ]] \
            || die "video media size mismatch: $storage_key"
    fi
    actual_hash="$(shasum -a 256 "$media_file" | awk '{print $1}')"
    [[ "$actual_hash" == "$expected_hash" ]] \
        || die "video media hash mismatch: $storage_key"
    verified_video_objects=$((verified_video_objects + 1))
done <"$video_manifest"

note "verified $verified_video_objects referenced video objects"

production_counts="$(
    prod_psql -X -At -F '|' -v ON_ERROR_STOP=1 -c "
      select count(*) from books
      union all select count(*) from content_blocks
      union all select count(*) from chunks
      union all select count(*) from chunk_embeddings
      union all select count(*) from video.videos
      union all select count(*) from video.frames
    " | paste -sd '|' -
)"
local_counts="$(
    psql "$local_database_url" -X -At -F '|' -v ON_ERROR_STOP=1 -c "
      select count(*) from books
      union all select count(*) from content_blocks
      union all select count(*) from chunks
      union all select count(*) from chunk_embeddings
      union all select count(*) from video.videos
      union all select count(*) from video.frames
    " | paste -sd '|' -
)"
[[ "$local_counts" == "$production_counts" ]] \
    || die "canonical or derived database counts differ from production"

local_storage_inventory="$(
    psql "$local_database_url" -X -At -F '|' -v ON_ERROR_STOP=1 -c "
      select count(*), coalesce(sum((metadata->>'size')::bigint), 0)
      from storage.objects where bucket_id = 'book-sources'
    "
)"
[[ "$local_storage_inventory" == "$copied_objects|$copied_bytes" ]] \
    || die "local Storage metadata does not match copied objects"

note "starting the local app and testing the local-only login"
"$ROOT/scripts/local.sh" up
local_password="$(
    security find-generic-password \
        -s "$KEYCHAIN_SERVICE" -a "$LOCAL_EMAIL" -w
)"
token_response="$(
    curl --fail --silent --show-error \
        --request POST "$local_supabase_url/auth/v1/token?grant_type=password" \
        --header "apikey: $local_service_key" \
        --header 'Content-Type: application/json' \
        --data "$(jq -cn \
            --arg email "$LOCAL_EMAIL" \
            --arg password "$local_password" \
            '{email:$email,password:$password}')"
)"
unset local_password
access_token="$(printf '%s' "$token_response" | jq -er '.access_token')"

book_count="$(
    curl --fail --silent --show-error \
        'http://127.0.0.1:8000/api/books?document_type=all' \
        --header "Authorization: Bearer $access_token" \
        | jq -er '.books | length'
)"
video_count="$(
    curl --fail --silent --show-error 'http://127.0.0.1:8000/api/videos?limit=100' \
        --header "Authorization: Bearer $access_token" \
        | jq -er '.videos | length'
)"
unset access_token token_response
[[ "$book_count" == "$expected_document_count" ]] \
    || die "local login expected $expected_document_count documents, found $book_count"
[[ "$video_count" == "$expected_video_count" ]] \
    || die "local login expected $expected_video_count videos, found $video_count"
"$ROOT/scripts/local.sh" doctor

note "production snapshot complete: $book_count documents and $video_count videos are available locally"
note "login email: $LOCAL_EMAIL"
note "password: security find-generic-password -s $KEYCHAIN_SERVICE -a $LOCAL_EMAIL -w"
