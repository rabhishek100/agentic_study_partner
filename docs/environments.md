# Environments

The app has three deliberately isolated tiers. Code may move between them;
databases, Auth users, Storage objects, and video volumes do not.

| Tier | App runtime | Supabase | Media storage | Purpose |
|---|---|---|---|---|
| local | native API/worker + Next.js | local CLI stack | `data/video-media` | development and integration tests |
| staging | Railway `staging` | `agentic-study-partner-staging` (`xtkbcireogbjjiuruvzp`) | staging `api-volume` | production-shaped acceptance testing |
| production | Railway `production` | production project | production `api-volume` | real data |

Never configure a local or staging runtime to use a production database URL,
service-role key, volume, or Storage endpoint. A deliberate read-only snapshot
into local is the sole exception: `scripts/clone_prod_to_local.sh` streams the
data into local services, verifies it, and discards production credentials
without writing them to disk. Railway variables are environment-scoped, and
the staging Supabase database password lives in macOS Keychain under
`agentic-study-partner-staging-db` rather than in this repository.

## Local environment

Prerequisites are Docker (Docker Desktop or Colima), Node/npm, and `uv`. The
first setup downloads the local Supabase services and installs locked
dependencies:

```bash
scripts/local.sh setup
scripts/local.sh up
scripts/local.sh doctor
```

The local web app is at `http://localhost:3000`, the API health endpoint is at
`http://localhost:8000/api/health`, and captured Auth email is at
`http://127.0.0.1:54324`. `setup` synchronizes local-only credentials into the
ignored `.env` and `frontend/.env.local`; it never prints their values.

The script runs the combined API/worker and web natively for fast reloads. It
keeps the application-complete Supabase services—Postgres, Auth, REST,
Storage, Realtime, gateway, metadata, and mail capture—and omits unused Studio,
analytics, edge-runtime, vector logging, and image-proxy images to save disk.

Useful operations:

```bash
scripts/local.sh logs app       # or: logs web
scripts/local.sh down           # preserve database and Storage data
scripts/local.sh reset          # destructive: rebuild only local DB data
scripts/local.sh up-containers  # optional fully containerized app runtime
```

`scripts/local_postgres.sh` remains a schema-only fallback. It cannot replace
the full stack for Auth, Storage, ingestion, or browser testing.

### Local production-library snapshot

For an exact local copy of the current library without repeating parsing or
model calls, first ensure at least 3 GiB is free and register a Railway SSH key.
Then run:

```bash
scripts/clone_prod_to_local.sh --yes
```

This is destructive only to the isolated local database, local `book-sources`
bucket, and `data/video-media`. Production is queried and streamed read-only.
The command copies canonical and derived Postgres rows, all private book PDFs,
and the Railway video volume; checks database counts, object sizes, and media
hashes; starts the app; and exercises authenticated book and video endpoints.

Production Auth rows, passwords, sessions, and tokens are not copied. The
primary owner receives a new local-only login at
`local-library@study-partner.test`; its generated password is held in macOS
Keychain:

```bash
security find-generic-password \
  -s agentic-study-partner-local-library \
  -a local-library@study-partner.test -w
```

The two legacy bootstrap books remain under the bootstrap owner in Postgres,
while the login-visible primary library contains the 54 distinct books and one
video present in production. Re-running the command refreshes the snapshot
from scratch and never merges production rows into existing local activity.

## Staging environment

Staging has its own public domains:

- web: `https://web-staging-f7bf.up.railway.app`
- API: `https://api-staging-5415.up.railway.app`

Its Supabase Auth Site URL and redirect allow-list contain only the staging
web origin. Its `LANGSMITH_PROJECT` is `agentic-study-partner-staging`, and
video cleanup initially runs with `VIDEO_CLEANUP_DRY_RUN=1`.

Deploy and verify:

```bash
scripts/staging.sh deploy
scripts/staging.sh wait
scripts/staging.sh doctor
```

The deploy script records the Git revision in each service. `doctor` requires
the API's canonical and retrieval readiness flags and checks the public web
page. A staging deploy is not a production deploy.

## Database and Auth changes

Apply migrations before deploying code that depends on them. Use the direct
staging connection retrieved from Keychain; runtime services use the session
pooler instead. The declarative staging Auth configuration is in
`ops/staging/supabase/config.toml` and must be pushed from its parent directory
so the CLI reads the intended file:

```bash
(cd ops/staging && npx --yes supabase@2.109.1 config push \
  --project-ref xtkbcireogbjjiuruvzp)
```

Do not run the root local `supabase/config.toml` against a hosted project; its
URLs and email-confirmation behavior are intentionally local.

## Promotion and rollback

Promotion is explicit: validate the branch locally, deploy it to staging, run
the smoke checks, merge it to `main`, and then separately deploy `main` to the
Railway production environment. Railway is not connected to GitHub, so a merge
does not deploy anything.

Roll code back by redeploying the last healthy Railway deployment. Schema
changes should remain backward-compatible during a release; data rollback is a
separate, deliberate operation and must never be inferred from a code rollback.
