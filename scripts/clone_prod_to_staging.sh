#!/usr/bin/env bash
# Replace staging application data with a verified, curated production snapshot.
#
# Production is read only. Production Auth is never copied. The selected corpus
# is intentionally bounded so the hosted staging project remains well below the
# Supabase Free database limit.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT/ops/staging/curated_documents.json"
SOURCE_ENVIRONMENT="${RAILWAY_PRODUCTION_ENVIRONMENT:-production}"
TARGET_ENVIRONMENT="${RAILWAY_STAGING_ENVIRONMENT:-staging}"
SERVICE="api"
TARGET_PROJECT_REF="xtkbcireogbjjiuruvzp"
TARGET_EMAIL="${STAGING_LIBRARY_EMAIL:-staging-library@study-partner.test}"
KEYCHAIN_SERVICE="agentic-study-partner-staging-library"
VIDEO_MOUNT="/var/lib/agentic-study-partner/video-media"
MAX_DATABASE_BYTES=$((350 * 1024 * 1024))

die() { echo "error: $*" >&2; exit 1; }
note() { echo "staging-clone: $*"; }
require() { command -v "$1" >/dev/null 2>&1 || die "$1 is required"; }

usage() {
    cat <<'EOF'
usage: scripts/clone_prod_to_staging.sh --yes

Destructively replaces only staging public/video rows, staging book-sources
objects, and the staging API video volume with the curated production fixture
declared in ops/staging/curated_documents.json.

Production is read only. Production Auth credentials and sessions are never
copied. The staging-only password is stored in macOS Keychain.
EOF
}

[[ "${1:-}" == "--yes" && $# -eq 1 ]] || { usage; exit 2; }

for command in curl jq openssl paste psql python3 railway security shasum tar; do
    require "$command"
done
[[ -f "$MANIFEST" ]] || die "curated document manifest is missing"
[[ "$SOURCE_ENVIRONMENT" == "production" ]] \
    || die "source Railway environment must be exactly production"
[[ "$TARGET_ENVIRONMENT" == "staging" ]] \
    || die "target Railway environment must be exactly staging"
[[ "$SOURCE_ENVIRONMENT" != "$TARGET_ENVIRONMENT" ]] \
    || die "source and target environments must differ"

selected_ids="$(jq -er 'if length > 0 and all(.[]; (.id | type) == "number") then map(.id) | join(",") else error("invalid manifest") end' "$MANIFEST")"
selected_count="$(jq -er 'length' "$MANIFEST")"
[[ "$selected_ids" =~ ^[0-9]+(,[0-9]+)*$ && "$selected_count" =~ ^[0-9]+$ ]] \
    || die "curated document IDs are unsafe"

note "loading production and staging connection settings without printing them"
source_json="$(railway variables --environment "$SOURCE_ENVIRONMENT" --service "$SERVICE" --json)"
target_json="$(railway variables --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" --json)"

source_database_url="$(printf '%s' "$source_json" | jq -er '.DATABASE_URL')"
source_supabase_url="$(printf '%s' "$source_json" | jq -er '.SUPABASE_URL')"
source_service_key="$(printf '%s' "$source_json" | jq -er '.SUPABASE_SERVICE_ROLE_KEY')"
target_database_url="$(printf '%s' "$target_json" | jq -er '.DATABASE_URL')"
target_supabase_url="$(printf '%s' "$target_json" | jq -er '.SUPABASE_URL')"
target_service_key="$(printf '%s' "$target_json" | jq -er '.SUPABASE_SERVICE_ROLE_KEY')"
unset source_json target_json

[[ "$source_supabase_url" == https://* && "$target_supabase_url" == https://* ]] \
    || die "source and target Supabase endpoints must use hosted HTTPS"
[[ "$target_supabase_url" == "https://$TARGET_PROJECT_REF.supabase.co" ]] \
    || die "target Supabase project is not the declared staging project"
[[ "$source_supabase_url" != "$target_supabase_url" \
   && "$source_database_url" != "$target_database_url" ]] \
    || die "source and target unexpectedly resolve to the same service"

connection_parts() {
    CLONE_DATABASE_URL="$1" python3 - <<'PY'
import os
from urllib.parse import unquote, urlparse

parsed = urlparse(os.environ["CLONE_DATABASE_URL"])
print("\t".join((
    parsed.hostname or "",
    str(parsed.port or 5432),
    parsed.username or "",
    unquote(parsed.password or ""),
    (parsed.path or "/postgres").lstrip("/"),
)))
PY
}

IFS=$'\t' read -r source_host source_port source_user source_password source_database \
    <<<"$(connection_parts "$source_database_url")"
IFS=$'\t' read -r target_host target_port target_user target_password target_database \
    <<<"$(connection_parts "$target_database_url")"
unset source_database_url target_database_url
[[ -n "$source_host" && -n "$source_user" && -n "$source_password" \
   && -n "$target_host" && -n "$target_user" && -n "$target_password" ]] \
    || die "could not parse database pooler connections"

source_psql() {
    PGHOST="$source_host" PGPORT="$source_port" PGUSER="$source_user" \
    PGPASSWORD="$source_password" PGDATABASE="$source_database" \
        command psql "$@"
}
target_psql() {
    PGHOST="$target_host" PGPORT="$target_port" PGUSER="$target_user" \
    PGPASSWORD="$target_password" PGDATABASE="$target_database" \
        command psql "$@"
}

# Buffer retryable read-only queries so a failed attempt can never leave
# partial output in a caller's command substitution or binary batch file.
source_query() {
    local attempt output error_file
    error_file="$(mktemp)"
    for attempt in 1 2 3; do
        if output="$(source_psql "$@" 2>"$error_file")"; then
            rm -f "$error_file"
            printf '%s\n' "$output"
            return 0
        fi
        echo "staging-clone: production read failed; retrying ($attempt of 3)" >&2
        sleep 2
    done
    cat "$error_file" >&2
    rm -f "$error_file"
    return 1
}
target_query() {
    local attempt output error_file
    error_file="$(mktemp)"
    for attempt in 1 2 3; do
        if output="$(target_psql "$@" 2>"$error_file")"; then
            rm -f "$error_file"
            printf '%s\n' "$output"
            return 0
        fi
        echo "staging-clone: staging read failed; retrying ($attempt of 3)" >&2
        sleep 2
    done
    cat "$error_file" >&2
    rm -f "$error_file"
    return 1
}

source_major="$(source_psql -X -At -v ON_ERROR_STOP=1 -c "select current_setting('server_version_num')::integer / 10000")"
target_major="$(target_psql -X -At -v ON_ERROR_STOP=1 -c "select current_setting('server_version_num')::integer / 10000")"
[[ "$source_major" == "$target_major" ]] \
    || die "production and staging PostgreSQL major versions must match"
target_psql -X -v ON_ERROR_STOP=1 -c \
    "set session_replication_role = replica; reset session_replication_role" \
    >/dev/null

primary_owner_id="$(
    source_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select owner_id from books group by owner_id order by count(*) desc limit 1"
)"
[[ "$primary_owner_id" =~ ^[0-9a-f-]{36}$ ]] \
    || die "could not resolve the production library owner"

manifest_contract="$(
    source_psql -X -At -F '|' -v ON_ERROR_STOP=1 -c "
      select count(*), count(*) filter (where status = 'ready'),
             count(distinct document_type), count(distinct owner_id)
      from books where id in ($selected_ids)
    "
)"
[[ "$manifest_contract" == "$selected_count|$selected_count|2|1" ]] \
    || die "curated manifest no longer resolves to ready books and papers"
manifest_owner="$(
    source_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select min(owner_id::text) from books where id in ($selected_ids)"
)"
[[ "$manifest_owner" == "$primary_owner_id" ]] \
    || die "curated documents do not belong to the primary production owner"

source_tables="$(
    source_psql -X -At -v ON_ERROR_STOP=1 -c "
      select schemaname || '.' || tablename from pg_tables
      where schemaname in ('public', 'video') order by 1
    "
)"
target_tables="$(
    target_psql -X -At -v ON_ERROR_STOP=1 -c "
      select schemaname || '.' || tablename from pg_tables
      where schemaname in ('public', 'video') order by 1
    "
)"
[[ "$source_tables" == "$target_tables" ]] \
    || die "production and staging application table manifests differ"

temporary_dir="$(mktemp -d)"
api_scaled_down=false
cleanup() {
    if [[ "${api_scaled_down:-false}" == true ]]; then
        echo "staging-clone: restoring staging API after interrupted clone" >&2
        railway scale --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
            us-west=1 >/dev/null 2>&1 || true
    fi
    [[ -d "${temporary_dir:-}" ]] \
        && find "$temporary_dir" -depth -delete 2>/dev/null || true
}
trap cleanup EXIT

note "pausing the staging API/worker during the database replacement"
railway scale --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
    us-west=0 >/dev/null
api_scaled_down=true

note "replacing the staging-only library login"
delete_user_status="$(
    curl --silent --show-error --output "$temporary_dir/delete-user.json" \
        --write-out '%{http_code}' --request DELETE \
        "$target_supabase_url/auth/v1/admin/users/$primary_owner_id" \
        --header "apikey: $target_service_key" \
        --header "Authorization: Bearer $target_service_key"
)"
[[ "$delete_user_status" == "200" || "$delete_user_status" == "404" ]] \
    || die "could not replace staging library user (HTTP $delete_user_status)"
target_password_value="$(openssl rand -hex 18)Aa1!"
create_user_status="$(
    curl --silent --show-error --output "$temporary_dir/create-user.json" \
        --write-out '%{http_code}' --request POST \
        "$target_supabase_url/auth/v1/admin/users" \
        --header "apikey: $target_service_key" \
        --header "Authorization: Bearer $target_service_key" \
        --header 'Content-Type: application/json' \
        --data "$(jq -cn --arg id "$primary_owner_id" --arg email "$TARGET_EMAIL" \
            --arg password "$target_password_value" \
            '{id:$id,email:$email,password:$password,email_confirm:true}')"
)"
[[ "$create_user_status" == "200" ]] \
    || die "staging library user creation failed (HTTP $create_user_status)"
security add-generic-password -U -s "$KEYCHAIN_SERVICE" -a "$TARGET_EMAIL" \
    -w "$target_password_value" >/dev/null
unset target_password_value

note "clearing only staging public/video application tables"
truncate_list="$(printf '%s\n' "$target_tables" | paste -sd ',' -)"
[[ "$truncate_list" =~ ^(public|video)\.[a-z0-9_]+(,(public|video)\.[a-z0-9_]+)*$ ]] \
    || die "unsafe staging table manifest"
target_psql -X -v ON_ERROR_STOP=1 -c \
    "truncate table $truncate_list restart identity cascade" >/dev/null

copy_table() {
    local table_name="$1" predicate="${2:-}" row_count columns batch_size offset
    local expected_after imported current_count
    [[ "$table_name" =~ ^(public|video)\.[a-z0-9_]+$ ]] \
        || die "unsafe production table name"
    [[ -z "$predicate" || "$predicate" =~ ^where\ [a-z0-9_.]+\ in\ \([0-9,]+\)$ ]] \
        || die "unsafe curated table predicate"
    row_count="$(
        source_query -X -At -v ON_ERROR_STOP=1 \
            -c "select count(*) from $table_name $predicate"
    )"
    columns="$(
        source_query -X -At -v ON_ERROR_STOP=1 -c "
          select string_agg(format('%I', attribute.attname), ', '
                            order by attribute.attnum)
          from pg_attribute attribute
          join pg_class relation on relation.oid = attribute.attrelid
          join pg_namespace namespace on namespace.oid = relation.relnamespace
          where namespace.nspname || '.' || relation.relname = '$table_name'
            and attribute.attnum > 0 and not attribute.attisdropped
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
    offset=0
    while (( offset < row_count )); do
        batch_file="$temporary_dir/table.copy"
        exported=false
        for attempt in 1 2 3; do
            if source_psql -X -q -v ON_ERROR_STOP=1 -c "
                copy (
                  select $columns from $table_name $predicate
                  order by ctid limit $batch_size offset $offset
                ) to stdout with (format binary)
              " >"$batch_file"; then
                exported=true
                break
            fi
            note "$table_name batch at $offset failed (attempt $attempt of 3)"
            sleep 2
        done
        [[ "$exported" == true ]] \
            || die "could not export $table_name batch at row $offset"
        expected_after=$((offset + batch_size))
        (( expected_after > row_count )) && expected_after="$row_count"
        imported=false
        for attempt in 1 2 3; do
            if {
                printf '%s\n' "set session_replication_role = replica;"
                printf "\\copy %s (%s) from '%s' with (format binary)\n" \
                    "$table_name" "$columns" "$batch_file"
            } | target_psql -X -v ON_ERROR_STOP=1 >/dev/null; then
                imported=true
                break
            fi
            current_count="$(
                target_query -X -At -v ON_ERROR_STOP=1 \
                    -c "select count(*) from $table_name"
            )"
            if [[ "$current_count" == "$expected_after" ]]; then
                note "$table_name batch at $offset committed before disconnect"
                imported=true
                break
            fi
            [[ "$current_count" == "$offset" ]] \
                || die "$table_name has an ambiguous row count after a failed batch"
            note "$table_name import at $offset failed (attempt $attempt of 3)"
            sleep 2
        done
        [[ "$imported" == true ]] \
            || die "could not import $table_name batch at row $offset"
        : >"$batch_file"
        offset=$((offset + batch_size))
    done
}

note "copying the bounded base snapshot"
while IFS= read -r table_name; do
    case "$table_name" in
        public.chunk_embeddings|public.deck_jobs|public.image_blocks|public.image_captions|video.evidence_embeddings)
            continue
            ;;
    esac
    copy_table "$table_name"
done <<<"$source_tables"

note "pruning non-curated books through database cascades"
target_psql -X -v ON_ERROR_STOP=1 -c \
    "delete from public.books where id not in ($selected_ids)" >/dev/null
target_psql -X -v ON_ERROR_STOP=1 -c "
  update public.books as book
  set cards_automation_eligible_at = null
  where book.id in ($selected_ids)
    and exists (
      select 1 from public.decks as deck
      where deck.owner_id = book.owner_id and deck.book_id = book.id
        and deck.status = 'ready'
    )
" >/dev/null

copy_table public.chunk_embeddings "where source_book_id in ($selected_ids)"
copy_table public.image_blocks "where book_id in ($selected_ids)"
copy_table public.image_captions "where book_id in ($selected_ids)"
copy_table video.evidence_embeddings

note "compacting pruned staging application tables"
target_psql -X -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
select format('vacuum (full, analyze) %I.%I', schemaname, tablename)
from pg_tables
where schemaname in ('public', 'video')
order by schemaname, tablename
\gexec
SQL

note "repairing staging sequences"
sequence_manifest="$(
    target_psql -X -At -F $'\t' -v ON_ERROR_STOP=1 -c "
      select namespace.nspname, relation.relname, attribute.attname
      from pg_class relation
      join pg_namespace namespace on namespace.oid = relation.relnamespace
      join pg_attribute attribute on attribute.attrelid = relation.oid
      where namespace.nspname in ('public', 'video')
        and relation.relkind = 'r' and attribute.attnum > 0
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
        || die "unsafe staging sequence owner"
    target_psql -X -v ON_ERROR_STOP=1 -c "
      select setval(
        pg_get_serial_sequence('$sequence_schema.$sequence_table', '$sequence_column'),
        coalesce(max($sequence_column), 1), max($sequence_column) is not null
      ) from $sequence_schema.$sequence_table
    " >/dev/null
done <<<"$sequence_manifest"

note "copying PDFs for curated documents"
while IFS= read -r old_object; do
    [[ "$old_object" =~ ^[0-9a-f-]{36}/[A-Za-z0-9._/-]+$ \
       && "$old_object" != *".."* ]] || die "unsafe staging Storage path"
    curl --fail --silent --show-error --request DELETE \
        "$target_supabase_url/storage/v1/object/book-sources/$old_object" \
        --header "apikey: $target_service_key" \
        --header "Authorization: Bearer $target_service_key" >/dev/null
done < <(
    target_psql -X -At -v ON_ERROR_STOP=1 -c \
        "select name from storage.objects where bucket_id = 'book-sources' order by name"
)

object_manifest="$temporary_dir/book-objects.tsv"
source_psql -X -At -F $'\t' -v ON_ERROR_STOP=1 -c "
  with paths as (
    select source_storage_bucket bucket, source_storage_path path, file_hash
    from books where id in ($selected_ids)
    union all
    select viewer_storage_bucket, viewer_storage_path, null::text
    from books where id in ($selected_ids) and viewer_storage_path is not null
  )
  select object.name, coalesce(object.metadata->>'mimetype', 'application/pdf'),
         object.metadata->>'size', coalesce(paths.file_hash, '')
  from paths join storage.objects object
    on object.bucket_id = paths.bucket and object.name = paths.path
  order by object.name
" >"$object_manifest"

copied_objects=0
copied_bytes=0
while IFS=$'\t' read -r object_path media_type expected_size expected_hash; do
    [[ "$object_path" =~ ^[0-9a-f-]{36}/[A-Za-z0-9._/-]+$ \
       && "$object_path" != *".."* ]] || die "unsafe production Storage path"
    object_file="$temporary_dir/object.pdf"
    curl --fail --silent --show-error --retry 3 \
        "$source_supabase_url/storage/v1/object/book-sources/$object_path" \
        --header "apikey: $source_service_key" \
        --header "Authorization: Bearer $source_service_key" \
        --output "$object_file"
    actual_size="$(wc -c <"$object_file" | tr -d ' ')"
    [[ "$actual_size" == "$expected_size" ]] \
        || die "PDF size mismatch for $object_path"
    if [[ -n "$expected_hash" ]]; then
        actual_hash="$(shasum -a 256 "$object_file" | awk '{print $1}')"
        [[ "$actual_hash" == "$expected_hash" ]] \
            || die "PDF hash mismatch for $object_path"
    fi
    curl --fail --silent --show-error --retry 3 --request POST \
        "$target_supabase_url/storage/v1/object/book-sources/$object_path" \
        --header "apikey: $target_service_key" \
        --header "Authorization: Bearer $target_service_key" \
        --header 'x-upsert: true' --header "Content-Type: $media_type" \
        --data-binary "@$object_file" >/dev/null
    copied_objects=$((copied_objects + 1))
    copied_bytes=$((copied_bytes + actual_size))
done <"$object_manifest"

note "restoring the staging API/worker after the consistent database import"
railway scale --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
    us-west=1 >/dev/null
api_scaled_down=false
api_domain="$(
    railway domain list --service "$SERVICE" --environment "$TARGET_ENVIRONMENT" --json \
        | jq -er '[.. | objects | .domain? // empty][0]'
)"
api_origin="https://$api_domain"
api_ready=false
for _ in $(seq 1 60); do
    if curl --fail --silent --show-error --output /dev/null \
        "$api_origin/api/health" 2>/dev/null; then
        api_ready=true
        break
    fi
    sleep 2
done
[[ "$api_ready" == true ]] || die "staging API did not restart after database import"

note "streaming the complete production video volume into staging"
source_video_inventory="$(
    railway ssh --environment "$SOURCE_ENVIRONMENT" --service "$SERVICE" \
        sh -lc "find '$VIDEO_MOUNT' -type f -exec stat -c %s {} \\; | awk \
          '{bytes += \$1; files += 1} END {print files \"|\" bytes}'"
)"
railway ssh --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
    sh -lc "find '$VIDEO_MOUNT' -mindepth 1 -depth -delete"
railway ssh --environment "$SOURCE_ENVIRONMENT" --service "$SERVICE" \
    tar -C "$VIDEO_MOUNT" -cf - . \
    | railway ssh --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
        tar -C "$VIDEO_MOUNT" -xf -
target_video_inventory="$(
    railway ssh --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
        sh -lc "find '$VIDEO_MOUNT' -type f -exec stat -c %s {} \\; | awk \
          '{bytes += \$1; files += 1} END {print files \"|\" bytes}'"
)"
[[ "$target_video_inventory" == "$source_video_inventory" ]] \
    || die "staging video volume inventory differs from production"

note "verifying every database-referenced video object on staging"
video_hash_manifest="$temporary_dir/video-hashes.txt"
target_psql -X -At -F $'\t' -v ON_ERROR_STOP=1 -c "
  select distinct storage_key, content_hash from (
    select storage_key, content_hash from video.video_sources where storage_key is not null
    union all select storage_key, content_hash from video.resources where storage_key is not null
    union all select full_storage_key, full_content_hash from video.frames where full_storage_key is not null
    union all select preview_storage_key, preview_content_hash from video.frames where preview_storage_key is not null
  ) objects order by storage_key
" | awk -F $'\t' '{print $2 "  " $1}' >"$video_hash_manifest"
railway ssh --environment "$TARGET_ENVIRONMENT" --service "$SERVICE" \
    sh -lc "cd '$VIDEO_MOUNT' && sha256sum -c - >/dev/null" <"$video_hash_manifest"

note "checking curated row counts and Free-plan headroom"
source_counts="$(
    source_psql -X -At -v ON_ERROR_STOP=1 -c "
      select count(*) from books where id in ($selected_ids)
      union all select count(*) from content_blocks where book_id in ($selected_ids)
      union all select count(*) from chunks where source_book_id in ($selected_ids)
      union all select count(*) from chunk_embeddings where source_book_id in ($selected_ids)
      union all select count(*) from image_blocks where book_id in ($selected_ids)
      union all select count(*) from video.videos
      union all select count(*) from video.frames
      union all select count(*) from video.evidence_embeddings
    " | paste -sd '|' -
)"
target_counts="$(
    target_psql -X -At -v ON_ERROR_STOP=1 -c "
      select count(*) from books
      union all select count(*) from content_blocks
      union all select count(*) from chunks
      union all select count(*) from chunk_embeddings
      union all select count(*) from image_blocks
      union all select count(*) from video.videos
      union all select count(*) from video.frames
      union all select count(*) from video.evidence_embeddings
    " | paste -sd '|' -
)"
[[ "$target_counts" == "$source_counts" ]] \
    || die "staging canonical or derived row counts differ from the curated source"

target_storage_inventory="$(
    target_psql -X -At -F '|' -v ON_ERROR_STOP=1 -c "
      select count(*), coalesce(sum((metadata->>'size')::bigint), 0)
      from storage.objects where bucket_id = 'book-sources'
    "
)"
[[ "$target_storage_inventory" == "$copied_objects|$copied_bytes" ]] \
    || die "staging Storage inventory differs from copied PDFs"

target_database_bytes="$(
    target_psql -X -At -v ON_ERROR_STOP=1 \
        -c "select pg_database_size(current_database())"
)"
(( target_database_bytes < MAX_DATABASE_BYTES )) \
    || die "staging database exceeds the 350 MiB safety ceiling"

note "testing the staging-only login and hosted APIs"
target_password_value="$(
    security find-generic-password -s "$KEYCHAIN_SERVICE" -a "$TARGET_EMAIL" -w
)"
token_response="$(
    curl --fail --silent --show-error --retry 10 --retry-delay 2 \
        --request POST "$target_supabase_url/auth/v1/token?grant_type=password" \
        --header "apikey: $target_service_key" \
        --header 'Content-Type: application/json' \
        --data "$(jq -cn --arg email "$TARGET_EMAIL" --arg password "$target_password_value" \
            '{email:$email,password:$password}')"
)"
unset target_password_value
access_token="$(printf '%s' "$token_response" | jq -er '.access_token')"
hosted_documents="$(
    curl --fail --silent --show-error --retry 10 --retry-delay 2 \
        "$api_origin/api/books?document_type=all" \
        --header "Authorization: Bearer $access_token" | jq -er '.books | length'
)"
hosted_videos="$(
    curl --fail --silent --show-error "$api_origin/api/videos?limit=100" \
        --header "Authorization: Bearer $access_token" | jq -er '.videos | length'
)"
[[ "$hosted_documents" == "$selected_count" ]] \
    || die "hosted staging expected $selected_count documents, found $hosted_documents"
[[ "$hosted_videos" == "1" ]] \
    || die "hosted staging expected one video, found $hosted_videos"

first_book_id="${selected_ids%%,*}"
signed_pdf_url="$(
    curl --fail --silent --show-error "$api_origin/api/books/$first_book_id/source" \
        --header "Authorization: Bearer $access_token" | jq -er '.url'
)"
pdf_status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
    --head "$signed_pdf_url")"
[[ "$pdf_status" == "200" ]] || die "hosted staging signed PDF did not return HTTP 200"

notification_href="$(
    curl --fail --silent --show-error "$api_origin/api/notifications?limit=20" \
        --header "Authorization: Bearer $access_token" \
        | jq -er '.notifications | map(select(.kind == "daily_cards_review")) | first | .href'
)"
[[ "$notification_href" == "/decks?review=today" ]] \
    || die "staging daily review notification does not point to today's review"
unset access_token token_response signed_pdf_url

note "curated staging snapshot complete"
note "documents: $selected_count; PDFs: $copied_objects; video volume: $target_video_inventory"
note "database bytes: $target_database_bytes (safety ceiling: $MAX_DATABASE_BYTES)"
note "login email: $TARGET_EMAIL"
note "password: security find-generic-password -s $KEYCHAIN_SERVICE -a $TARGET_EMAIL -w"
