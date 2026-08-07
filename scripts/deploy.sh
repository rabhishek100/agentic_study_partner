#!/usr/bin/env bash
#
# Deploy one service, and record what was deployed.
#
#   scripts/deploy.sh api     API and ingestion worker, one container
#   scripts/deploy.sh web
#
# The service is named `api` for historical reasons and runs both processes:
# they have to share one media volume, and a Railway volume mounts to exactly
# one service. `worker` exists in the project from the books-only split and has
# nothing deployed to it; `app` is not a service in this project at all, and
# deploying to it fails rather than doing anything surprising.
#
# Two things this exists for, both learned the hard way.
#
# The worker once ran five commits behind the repository for hours, and the
# only way to notice was comparing a deployment timestamp against a git log by
# eye. `railway up` uploads a directory, so nothing in the image knows which
# commit it came from; setting BUILD_REVISION as a service variable is what
# makes /api/health and the worker's startup line able to answer.
#
# And `web` does not build from the repository root. Deploying it the way api
# and worker are deployed puts the Python API image on it, which crash-loops on
# `node server.js` and takes the site down. That happened. `--path-as-root` is
# the difference.

set -euo pipefail

service="${1:-}"
if [[ -z "$service" ]]; then
    echo "usage: scripts/deploy.sh <api|web|worker>" >&2
    exit 2
fi

revision="$(git rev-parse --short HEAD)"
if ! git diff --quiet || ! git diff --cached --quiet; then
    revision="${revision}-dirty"
    echo "warning: deploying with uncommitted changes; recorded as ${revision}" >&2
fi
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "deploying ${service} at ${revision}"
railway variable set --service "$service" "BUILD_REVISION=${revision}" >/dev/null
railway variable set --service "$service" "BUILD_TIME=${built_at}" >/dev/null

case "$service" in
    web)
        # The link that decides the upload root lives at the repository root,
        # so the path has to be made explicit; see the comment above.
        railway up ./frontend --path-as-root --service web --detach
        ;;
    *)
        railway up --service "$service" --detach
        ;;
esac

echo "deployed ${service} at ${revision}; confirm with:"
echo "  curl -s https://web-production-8529e.up.railway.app/api/health | jq"
