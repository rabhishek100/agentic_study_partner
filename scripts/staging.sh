#!/usr/bin/env bash
# Operate the already-provisioned, isolated Railway staging environment.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVIRONMENT="${RAILWAY_STAGING_ENVIRONMENT:-staging}"

die() { echo "error: $*" >&2; exit 1; }

domain_for() {
    local service="$1"
    railway domain list --service "$service" --environment "$ENVIRONMENT" --json \
        | jq -r '[.. | objects | .domain? // empty][0] // empty'
}

status() {
    railway service list --environment "$ENVIRONMENT" --json \
        | jq 'map({name, latestDeployment: (.latestDeployment.status // "not deployed")})'
}

deploy() {
    "$ROOT/scripts/deploy.sh" api "$ENVIRONMENT"
    "$ROOT/scripts/deploy.sh" web "$ENVIRONMENT"
    echo "uploads queued; run scripts/staging.sh wait"
}

wait_for_service() {
    local service="$1" status_value
    for _ in $(seq 1 60); do
        status_value="$(railway deployment list --service "$service" \
            --environment "$ENVIRONMENT" --limit 1 --json \
            | jq -r '.[0].status // "MISSING"')"
        case "$status_value" in
            SUCCESS) echo "${service}: SUCCESS"; return ;;
            FAILED|CRASHED|REMOVED) die "${service}: ${status_value}" ;;
        esac
        sleep 5
    done
    die "${service}: deployment did not finish within five minutes"
}

wait_for_deployments() {
    wait_for_service api
    wait_for_service web
}

doctor() {
    local api_domain web_domain revision
    api_domain="$(domain_for api)"
    web_domain="$(domain_for web)"
    [[ -n "$api_domain" && -n "$web_domain" ]] \
        || die "staging service domains are missing"
    revision="$(curl --fail --silent --show-error \
        "https://${api_domain}/api/health" \
        | jq -r 'select(.status == "ok" and .canonical_database_ready and .retrieval_database_ready) | .build_revision')"
    [[ -n "$revision" ]] || die "staging API is not ready"
    curl --fail --silent --show-error --output /dev/null "https://${web_domain}" \
        || die "staging web is not reachable"
    echo "staging healthy at ${revision}"
    echo "web: https://${web_domain}"
    echo "api: https://${api_domain}/api/health"
}

case "${1:-status}" in
    status) status ;;
    deploy) deploy ;;
    wait) wait_for_deployments ;;
    doctor) doctor ;;
    *) echo "usage: scripts/staging.sh <status|deploy|wait|doctor>" >&2; exit 2 ;;
esac
