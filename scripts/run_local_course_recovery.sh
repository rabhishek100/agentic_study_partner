#!/usr/bin/env bash
#
# Run the video recovery worker against the LOCAL database, reading source
# media from a remote R2 bucket.
#
# The first version of this script sourced .env, then pulled Railway variables
# for *both* staging and production and exported them together: production
# credentials as the acquisition source, staging credentials as the media
# write target, against whatever database .env happened to name. Three
# environments in one process, none of them stated, and nothing checking that
# the database being written to was the local one. That is how a recovery run
# reaches production by accident.
#
# So every environment is now named on the command line, the database is
# verified to be local before anything else happens, and the write target
# defaults to the local filesystem rather than to anyone's bucket.
#
#   scripts/run_local_course_recovery.sh --source production -- --max-stages 4
#
set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

SOURCE_ENVIRONMENT=""
MEDIA_TARGET="local"
WORKER_ARGUMENTS=()

usage() {
    cat >&2 <<'USAGE'
usage: run_local_course_recovery.sh --source <staging|production>
                                   [--media <local|staging>]
                                   [-- <worker arguments>]

  --source   Which R2 bucket the original media is READ from. Required and
             never defaulted: reading production is a deliberate act.
  --media    Where recovered media is WRITTEN. "local" (default) uses the
             filesystem media store and touches no bucket. "staging" writes
             to the staging bucket. Production is not accepted.
USAGE
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --source) SOURCE_ENVIRONMENT="${2:-}"; shift 2 ;;
        --media) MEDIA_TARGET="${2:-}"; shift 2 ;;
        --) shift; WORKER_ARGUMENTS=("$@"); break ;;
        -h|--help) usage ;;
        *) echo "unknown argument: $1" >&2; usage ;;
    esac
done

case "$SOURCE_ENVIRONMENT" in
    staging|production) ;;
    "") echo "error: --source is required (staging or production)" >&2; usage ;;
    *) echo "error: --source must be staging or production" >&2; exit 2 ;;
esac

case "$MEDIA_TARGET" in
    local|staging) ;;
    production)
        echo "error: refusing to write recovered media into production" >&2
        exit 2 ;;
    *) echo "error: --media must be local or staging" >&2; exit 2 ;;
esac

set -a
# shellcheck disable=SC1091
source .env
set +a

# --- The database must be the local one. -------------------------------------
# Checked before any credential is fetched, so a misconfigured .env cannot get
# as far as holding production keys in this process.
DATABASE_TARGET="${DATABASE_URL:-}"
if [ -z "$DATABASE_TARGET" ]; then
    echo "error: DATABASE_URL is not set" >&2
    exit 2
fi
DATABASE_HOST=$(printf '%s' "$DATABASE_TARGET" | sed -E 's|^[^/]*//||; s|^[^@]*@||; s|[:/?].*$||')
case "$DATABASE_HOST" in
    127.0.0.1|localhost|::1|"[::1]") ;;
    *)
        echo "error: DATABASE_URL points at '$DATABASE_HOST', not the local database." >&2
        echo "       This script only runs local recovery. Refusing." >&2
        exit 2 ;;
esac

# --- Cleanup stays inert for the whole run. ----------------------------------
# Recovery reconstructs rows while media is still arriving, which is precisely
# the state the orphan sweep would misread.
export VIDEO_CLEANUP_DRY_RUN=1

if [ -z "${VIDEO_RECOVERY_OWNER_ID:-}" ]; then
    echo "error: VIDEO_RECOVERY_OWNER_ID must be set explicitly." >&2
    echo "       It says whose media is read out of the source bucket, and a" >&2
    echo "       hardcoded default is the wrong way to answer that." >&2
    exit 2
fi

railway_variables() {
    railway variables --service api --environment "$1" --json
}

require_value() {
    local blob="$1" name="$2" environment="$3" value
    value=$(printf '%s' "$blob" | jq -r --arg key "$name" '.[$key] // ""')
    if [ -z "$value" ] || [ "$value" = "null" ]; then
        echo "error: $name is missing from the $environment environment" >&2
        exit 1
    fi
    printf '%s' "$value"
}

echo "recovery source bucket: $SOURCE_ENVIRONMENT (read only)" >&2
echo "recovered media target: $MEDIA_TARGET" >&2
echo "local database host:    $DATABASE_HOST" >&2

SOURCE_VARIABLES=$(railway_variables "$SOURCE_ENVIRONMENT")

# The acquisition source. Read only: the worker fetches original media from
# this bucket and never writes back to it.
export VIDEO_RECOVERY_S3_ENDPOINT=$(require_value "$SOURCE_VARIABLES" VIDEO_S3_ENDPOINT "$SOURCE_ENVIRONMENT")
export VIDEO_RECOVERY_S3_REGION=$(require_value "$SOURCE_VARIABLES" VIDEO_S3_REGION "$SOURCE_ENVIRONMENT")
export VIDEO_RECOVERY_S3_BUCKET=$(require_value "$SOURCE_VARIABLES" VIDEO_S3_BUCKET "$SOURCE_ENVIRONMENT")
export VIDEO_RECOVERY_S3_ACCESS_KEY_ID=$(require_value "$SOURCE_VARIABLES" VIDEO_S3_ACCESS_KEY_ID "$SOURCE_ENVIRONMENT")
export VIDEO_RECOVERY_S3_SECRET_ACCESS_KEY=$(require_value "$SOURCE_VARIABLES" VIDEO_S3_SECRET_ACCESS_KEY "$SOURCE_ENVIRONMENT")

# The media store the pipeline writes into.
if [ "$MEDIA_TARGET" = "local" ]; then
    export VIDEO_MEDIA_BACKEND=filesystem
    unset VIDEO_S3_ENDPOINT VIDEO_S3_REGION VIDEO_S3_BUCKET \
          VIDEO_S3_ACCESS_KEY_ID VIDEO_S3_SECRET_ACCESS_KEY || true
else
    MEDIA_VARIABLES=$(railway_variables staging)
    export VIDEO_MEDIA_BACKEND=r2
    export VIDEO_MEDIA_CACHE_ROOT=${VIDEO_MEDIA_CACHE_ROOT:-/tmp/study-partner-r2-cache-local-course}
    export VIDEO_S3_ENDPOINT=$(require_value "$MEDIA_VARIABLES" VIDEO_S3_ENDPOINT staging)
    export VIDEO_S3_REGION=$(require_value "$MEDIA_VARIABLES" VIDEO_S3_REGION staging)
    export VIDEO_S3_BUCKET=$(require_value "$MEDIA_VARIABLES" VIDEO_S3_BUCKET staging)
    export VIDEO_S3_ACCESS_KEY_ID=$(require_value "$MEDIA_VARIABLES" VIDEO_S3_ACCESS_KEY_ID staging)
    export VIDEO_S3_SECRET_ACCESS_KEY=$(require_value "$MEDIA_VARIABLES" VIDEO_S3_SECRET_ACCESS_KEY staging)
fi

# Model credentials come from staging in either case: recovery must never
# spend against a production budget.
MODEL_VARIABLES=$(railway_variables staging)
for variable in \
    OPENROUTER_API_KEY \
    OPENROUTER_AUDIO_MODEL \
    OPENROUTER_AUDIO_COST_PER_MINUTE_USD \
    OPENROUTER_VIDEO_VISION_MODEL \
    OPENROUTER_VIDEO_TEXT_EMBEDDING_MODEL \
    OPENROUTER_VIDEO_IMAGE_EMBEDDING_MODEL
do
    export "$variable=$(require_value "$MODEL_VARIABLES" "$variable" staging)"
done

exec .venv/bin/python -m worker.recover_video_course \
    ${WORKER_ARGUMENTS[@]+"${WORKER_ARGUMENTS[@]}"}
