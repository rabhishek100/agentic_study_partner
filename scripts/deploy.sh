#!/usr/bin/env bash
#
# Deploy one service, and record what was deployed.
#
#   scripts/deploy.sh api [environment]   API and worker, one container
#   scripts/deploy.sh web [environment]
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
environment="${2:-}"
if [[ -z "$service" ]]; then
    echo "usage: scripts/deploy.sh <api|web> [environment]" >&2
    exit 2
fi

if [[ "$service" == "worker" ]]; then
    echo "worker is vestigial; deploy api, which runs the combined worker" >&2
    exit 2
fi
if [[ "$service" != "api" && "$service" != "web" ]]; then
    echo "usage: scripts/deploy.sh <api|web> [environment]" >&2
    exit 2
fi

revision="$(git rev-parse --short HEAD)"
if ! git diff --quiet || ! git diff --cached --quiet; then
    revision="${revision}-dirty"
    echo "warning: deploying with uncommitted changes; recorded as ${revision}" >&2
fi
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Expanded below with the `${arr[@]+...}` guard rather than a bare
# `"${arr[@]}"`. Under `set -u`, bash 3.2 — which is what /bin/bash still is on
# macOS — treats a bare expansion of an *empty* array as an unbound variable and
# aborts. So `scripts/deploy.sh web`, the form this script's own usage line
# documents, died at the first expansion with `environment_args[@]: unbound
# variable` and never uploaded anything; only `scripts/deploy.sh web production`
# worked. The guarded form expands to nothing when the array is empty and to the
# quoted elements when it is not.
environment_args=()
if [[ -n "$environment" ]]; then
    environment_args=(--environment "$environment")
fi

echo "deploying ${service} at ${revision}${environment:+ to ${environment}}"
# `--skip-deploys` on both. Setting a service variable triggers a redeploy of
# whatever is currently deployed, so recording the revision queued two builds of
# the *old* source seconds before the real upload — and then the upload waited
# behind them for a build slot. Every past deploy in `railway deployment list`
# shows the same three entries, two of them REMOVED. On 2026-08-19, with
# Railway's builders degraded, the two spurious builds wedged and the real
# deployment failed without ever being assigned a build.
railway variable set --skip-deploys --service "$service" \
    ${environment_args[@]+"${environment_args[@]}"} "BUILD_REVISION=${revision}" >/dev/null
railway variable set --skip-deploys --service "$service" \
    ${environment_args[@]+"${environment_args[@]}"} "BUILD_TIME=${built_at}" >/dev/null

case "$service" in
    web)
        # The link that decides the upload root lives at the repository root,
        # so the path has to be made explicit; see the comment above.
        railway up ./frontend --path-as-root --service web \
            ${environment_args[@]+"${environment_args[@]}"} --detach
        ;;
    *)
        railway up --service "$service" ${environment_args[@]+"${environment_args[@]}"} --detach
        ;;
esac

echo "uploaded ${service} at ${revision}; wait for Railway, then verify health"
if [[ -n "$environment" ]]; then
    echo "  railway deployment list --service ${service} --environment ${environment} --limit 1"
fi
