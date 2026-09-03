# Railway Postgres, R2, and Supabase Auth migration runbook

## Assignment for a fresh-context agent

Take this repository from its current Supabase-coupled production deployment to
the target architecture below. Work end to end: inspect and reconcile the
source data, implement the provider boundaries and migration tooling, exercise
the migration against an isolated target, provision the approved production
resources, migrate and verify every object and database row, cut production
over, run the acceptance suite, establish backups, update the documentation,
and leave a rollback-capable handoff.

Do not treat this document as evidence that external state is unchanged. Read
`AGENTS.md`, inspect the repository and the live services, and remeasure every
baseline before writing to a provider. Do not print or commit credentials.

## Outcome

```text
Browser
  |-- sign-in/session ----------> new Supabase Free Auth project
  |-- application API ---------> Railway web/API
  |                                |
  |                                |-- private DATABASE_URL
  |                                v
  |                           Railway Postgres 17 + pgvector
  |
  `-- short-lived upload URL ---> private Cloudflare R2 book-source bucket

Cloudflare R2
  |-- book source PDFs (private, authoritative)
  |-- book figures (private, authoritative)
  `-- video source/evidence media (private, authoritative)
```

Supabase must hold identities only. It must not hold application tables,
chunks, embeddings, source PDFs, figures, or video media. Railway Postgres is
the canonical application database. R2 is the canonical home for large
objects. Embeddings remain in Postgres because they are queryable derived data,
fit comfortably after the completed size reductions, and do not justify a
second retrieval system.

## Measured starting point

These measurements were taken on 2026-09-03. Re-run them before acting.

- The Railway `api` and `web` services are running, but the production
  `DATABASE_URL` still points to Supabase, not to Railway Postgres.
- Hosted production Supabase is PostgreSQL 17.6, is about **1,571 MB**, has two
  `auth.users` rows, and has migrations only through `20260829130000`.
- The recovered local full-corpus database is PostgreSQL 17.6, is about
  **266 MB**, has three local/shadow auth rows, and has migrations through
  `20260903170000`.
- The local database has 4,526 `image_blocks`, all with object-storage keys.
  The inline base64 column has already been dropped locally.
- The local database has 6,820 book embeddings at 3,072 dimensions and 11,295
  video evidence embeddings at 768 or 1,024 dimensions, all stored as
  `halfvec` by the latest migration.
- The hosted database has not received the book-image and `halfvec` migrations.
- There are 54 source PDFs, historically about 464.7 MB, in the private
  Supabase `book-sources` bucket.
- Video media is already in Cloudflare R2. Book figures have been backfilled to
  a separate R2 bucket locally, but the current production service does not yet
  carry the `BOOK_IMAGE_*` variables or the code revision that reads them.
- Railway production still has an attached 5 GB API volume using roughly
  586 MB. Treat its contents as potentially live until all referenced media
  has been inventoried; never delete it as part of the database migration.
- Railway deployment is manual. Merging `main` does not deploy anything.

The older figures in `docs/supabase-migration-readiness.md` describe the state
before the 2026-09-03 image and embedding migrations. They are useful history,
not the source of truth for the new target.

## Fixed decisions

1. Use **Railway Postgres 17 with pgvector** for the application database.
2. Use a **new Supabase Free organization/project for Auth only**. A project in
   the currently restricted organization is not an acceptable dependency.
3. Use **three distinct private R2 buckets** (or preserve the already-created
   equivalents): video media, book figures, and book source/viewer PDFs. The
   video cleanup sweep must never be able to enumerate book objects.
4. Keep vectors in Postgres. Do not add Pinecone, Qdrant, Chroma, or another
   vector service.
5. Preserve the primary owner's UUID. It is embedded in foreign keys and R2
   object paths. Do not generate a replacement owner ID and rewrite the corpus.
6. Continue routing all application data through FastAPI. No browser connects
   directly to Railway Postgres.
7. Do not self-host the full Supabase stack on Railway. It adds services,
   maintenance, and cost without serving this project's portfolio goals.
8. A short maintenance window is acceptable. Do not implement dual database
   writes for this one-time move.

## Safety invariants

- Source systems are read-only until the target has passed acceptance.
- Never run a worker or cleanup sweep against a bucket unless the database it
  uses is the authoritative index for that bucket.
- Set `VIDEO_CLEANUP_DRY_RUN=1` throughout migration and the first production
  observation window.
- Add the equivalent dry-run/authoritativeness guard for book-source cleanup
  before switching that cleanup to R2.
- Never expose Supabase service-role or R2 credentials to the frontend.
- Presigned URLs are bearer credentials: make them single-object,
  content-type-bound, and short-lived.
- Every copied object is read or headed back and checked against expected size
  and SHA-256 before its database row changes provider.
- Run `pg_restore` with `--exit-on-error`; a restore with warnings or skipped
  vector columns is a failed restore.
- Do not copy Supabase ownership/role definitions into Railway. Create the
  target schema from repository migrations and copy data into it.
- Do not delete the old Supabase project, old Storage objects, local recovered
  database, local dumps, or old R2 objects during cutover.
- Do not change Railway billing limits, create paid resources, delete services,
  or retire Supabase without explicit user approval.
- Never run the test suite against a database holding the real corpus. It
  claims live ingestion jobs, and it deletes `book-sources` objects through
  the Storage HTTP API with the service-role key. Pointing tests at a separate
  database does not protect the bucket: the Storage service serves one
  database regardless of what `DATABASE_URL` says. On 2026-09-02 a suite run
  removed 52 of 54 freshly restored PDFs, and it surfaced only later as a 404
  on a book's source.

## User-approval gates

Stop and ask before each of these external changes if approval has not already
been given in the active conversation:

1. Creating the Railway pgvector service and its volume.
2. Creating a new Supabase organization/project.
3. Creating R2 buckets or credentials.
4. Taking the production API offline for cutover.
5. Enabling a backup/PITR feature that can add recurring cost.
6. Deleting or pausing the old Supabase project after the rollback window.

The agent may perform read-only inspections, write repository code and tests,
create local dumps, and run isolated local rehearsals before these gates.

## Definition of done

The migration is complete only when all of the following are true:

- Production API reports canonical and retrieval readiness against Railway
  Postgres.
- The database host classification is Railway and the API has no runtime
  connection to Supabase Postgres.
- Every repository migration through the current head is recorded on Railway.
- Counts, primary-key sets, canonical hashes, foreign-key checks, and vector
  dimensions match the chosen authoritative source.
- Existing user credentials work against the new Supabase Auth project, or a
  deliberately issued replacement password has been tested.
- A newly created Auth user can make an authenticated API request without a
  foreign-key failure in Railway.
- Books, papers, PDFs, figures, videos, courses, decks, conversations,
  interviews, and notifications work for the primary owner.
- A second account cannot enumerate or retrieve the primary owner's data.
- Every source/viewer PDF and book figure referenced by Railway is present in
  the correct R2 bucket with matching size and hash.
- A new PDF can be uploaded directly to R2, completed, ingested, opened in the
  viewer, queried, and cleaned up according to policy.
- `SUPABASE_SERVICE_ROLE_KEY` is no longer needed by the API or worker.
- The frontend contains no Supabase Storage/TUS endpoint.
- Scheduled Railway volume backups are enabled, an off-provider logical dump
  exists, and one restore drill has succeeded.
- Production documentation and environment examples describe the deployed
  architecture accurately.

## Phase 0: orientation and current-state capture

Read these first:

- `AGENTS.md`
- `README.md`
- `docs/environments.md`
- `docs/deployment.md`
- `docs/supabase-migration-readiness.md`
- `docs/recovery-2026-09-02.md`
- `docs/outage-recovery-2026-09-02.md`
- `supabase/local/supabase_shim.sql`
- the three migrations dated `20260903`
- `api/auth.py`
- `frontend/lib/supabase.ts`
- `frontend/hooks/use-session.ts`
- `frontend/components/auth-gate.tsx`
- `frontend/components/upload-panel.tsx`
- `ingestion/storage_objects.py`
- `storage/book_images.py`
- `video/media_store.py`
- `scripts/clone_prod_to_local.sh`
- `scripts/deploy.sh`

Capture, without exposing values:

- current branch, commit, and dirty state;
- production and staging Railway service/deployment IDs and health;
- deployed `BUILD_REVISION` from `/api/health`;
- names (not values) of relevant Railway variables;
- database host class, PostgreSQL and pgvector versions, database size, latest
  migration, user count, table sizes, and row counts;
- R2 bucket names, object counts, byte totals, and referenced/missing/orphaned
  counts;
- old Supabase project health for `db`, `auth`, and `storage` separately;
- current Supabase organization restriction and billing-cycle reset date.

Create a timestamped, gitignored evidence directory below
`artifacts/migration/`. Store manifests, counts, checksums, command logs with
secrets redacted, and the final migration report there.

Do not rely on `n_live_tup` for acceptance counts; use exact `count(*)`.

## Phase 1: establish the authoritative source

The local database is the presumptive migration source because it contains the
latest size-reduction migrations and the recovered full corpus. It cannot be
declared authoritative merely because it is smaller.

### 1.0 Two traps that make a restore fail quietly

Both were hit on 2026-09-02. Neither announces itself.

**The `pg_restore` on `PATH` is 14.x and cannot read these dumps.** It fails
with `unsupported version (1.16) in file header`, which reads like a corrupt
archive. Use PostgreSQL 17 binaries explicitly:

```bash
PG17=/opt/homebrew/opt/postgresql@17/bin
```

**A dump of the Supabase database is not self-sufficient.** Vector columns are
declared `extensions.vector`, and the archive contains neither the
`extensions` schema nor the extension, because Supabase's platform provides
them. Restoring into an empty database therefore fails on
`public.chunk_embeddings` and `video.evidence_embeddings` and cascades into
about 26 further errors — leaving a database that looks restored and has lost
**every embedding**. Create them first:

```sql
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;
```

This is the failure `--exit-on-error` exists to catch; know the cause so the
error is fixed rather than skipped.

### 1.1 Reconcile local and hosted databases

For every `public` and `video` table, compare:

- exact row count;
- ordered primary-key digest;
- digest of canonical/business columns, excluding deliberately mutable
  timestamps only when the exclusion is documented;
- maximum `created_at`/`updated_at` where present;
- owner-ID set;
- dangling foreign-key counts;
- ingestion jobs in active or retryable states.

Give special treatment to:

- `books`, `nodes`, `content_blocks`, `table_blocks`, `image_blocks`;
- `chunks`, `chunk_embeddings`, and active chunk builds;
- video/course canonical rows, transcript segments, evidence units, frames,
  resources, and evidence embeddings;
- decks, review state, conversations, interview sessions, and notifications;
- `auth.users` identity/owner IDs, without logging emails unnecessarily.

If hosted Supabase contains newer rows that do not exist locally, stop and
write a reconciliation proposal. Do not overwrite them. If the hosted database
is inaccessible, record the last known recovery point and use the recovered
local corpus only after the user accepts that recovery point.

### 1.2 Quiesce mutable local state

Before the final source dump:

- stop local API and worker processes;
- confirm no orphaned `worker.main` process remains;
- confirm no active ingestion lease or interview write is running;
- set all cleanup jobs to dry-run;
- take a custom-format PostgreSQL 17 dump and a separate schema-only dump;
- write SHA-256 checksums for both;
- export exact validation manifests and R2 inventories.

Keep this frozen source dump until the rollback window closes.

## Phase 2: implement the provider-neutral boundaries

Do this work on a `codex/` branch. Keep changes reviewable and split commits by
concern. Do not mix external cutover with unreviewed application features.

### 2.1 Generic Postgres bootstrap and migration runner

Add a provider-neutral bootstrap under `ops/postgres/` and a runner under
`scripts/`. It must:

1. Require PostgreSQL 17.
2. Create `extensions`, `auth`, `storage`, and `supabase_migrations` only as
   compatibility schemas needed by historical migrations.
3. Install `vector` into `extensions`, plus `pgcrypto` and `uuid-ossp`.
4. Refuse pgvector versions that do not support `halfvec`.
5. Create the minimal `auth.users`, `auth.uid()`, `storage.buckets`,
   `storage.objects`, and `storage.foldername()` compatibility objects required
   to replay historical migrations.
6. Create the historical `anon`, `authenticated`, and `service_role` roles only
   if migrations still require them. They must have no login and no database
   credentials.
7. Apply migrations exactly once in filename order and record each version in
   `supabase_migrations.schema_migrations`.
8. Use `current_database()` rather than assuming the database is named
   `postgres`.
9. Exit on the first SQL error.
10. Print object/version summaries, never connection strings.

Prefer extracting shared SQL from `supabase/local/supabase_shim.sql` over
maintaining two drifting copies. Keep local Supabase integration tests working.

Add a target audit that checks:

- all expected tables, functions, constraints, indexes, and extensions;
- latest migration equals repository head;
- embedding columns are `extensions.halfvec`;
- book embedding dimensions are 3,072;
- video dimensions are only 768/1,024;
- every figure row has a non-null backend, key, hash, and positive size;
- no `base64_content` column remains;
- no product dependency on `pg_net` or `supabase_vault` exists.

### 2.2 Auth-owned versus application-owned users

Keep UUID ownership stable and keep the first migration small.

For this cutover, `auth.users` in Railway is a **shadow application identity
registry**, not Supabase's internal Auth table. Preserve the existing foreign
keys rather than rewriting every tenant table during the infrastructure move.

Add an idempotent server-side `ensure_application_user()` operation:

- called only after signature, issuer, audience, expiry, role, and UUID subject
  have been verified;
- inserts the verified UUID/email into Railway `auth.users` on first use;
- updates only safe profile fields for an existing identical UUID;
- never maps an existing email to a different UUID automatically;
- fails closed on an ID/email collision;
- is exercised before any endpoint can insert a tenant-owned row;
- has concurrency tests for two simultaneous first requests.

Do not trust request bodies or frontend state for the owner. The verified JWT
subject remains the only source of `owner_id`.

Keep the current Supabase verifier for this migration but rename configuration
internals so Auth and Storage are no longer implicitly the same provider:

- `AUTH_SUPABASE_URL`
- `AUTH_JWT_ISSUER`
- `AUTH_JWT_AUDIENCE`
- `AUTH_JWT_SECRET` only for local HS256

For one compatibility release, fall back to the old `SUPABASE_*` names and log
a deprecation warning without values. Remove that fallback only after all
environments have moved.

The API no longer needs a Supabase service-role key once source storage has
moved to R2. Delete that runtime dependency and corresponding documentation.

### 2.3 RLS and database roles

The browser never connects to Railway Postgres and the repository already
filters application queries by verified `owner_id`. Historical Supabase RLS
policies cannot authenticate requests on an ordinary private Postgres
connection because PostgREST is no longer setting JWT claims.

Implement and document one consistent model:

- Railway Postgres is private and accepts only the API/worker runtime role and
  an operator/migration role.
- The runtime role is not the database superuser.
- Revoke public schema/table access not required by the runtime.
- Drop or disable the Supabase `auth.uid()` RLS policies on application tables
  in the Railway deployment path; retain explicit application-level owner
  predicates and their cross-user tests.
- Do not pretend inactive policies provide isolation.

Keep the compatibility schemas until historical migrations no longer need
them. A later cleanup can rename `auth.users` to `app_users`; that rename is not
part of this cutover unless tests show the shadow table cannot work safely.

Add a CI/static test that fails when a new request-serving repository query on
a tenant-owned table omits owner scoping. Preserve the existing multi-user
integration suite.

### 2.4 Provider-neutral book-source storage

Replace `ingestion/storage_objects.py` with a provider-neutral interface and
explicit implementations for:

- local filesystem/local Supabase, as needed by current integration tests;
- legacy hosted Supabase, for read-only migration and rollback;
- Cloudflare R2 through its S3-compatible API.

The interface needs `head`, `open/download`, `put`, `delete`, paginated list,
presigned GET, and presigned PUT. Preserve the existing safe error taxonomy.

Add backend columns so migration can be row-by-row and reversible:

- `ingestion_jobs.storage_backend`;
- `books.source_storage_backend`;
- `books.viewer_storage_backend`.

Backfill existing rows as `supabase`. New rows use the configured default.
Keep bucket/path columns; their names are provider-neutral enough. Require
backend/bucket/path to be all-null or all-present as appropriate.

Use a dedicated private R2 bucket for source and viewer PDFs. Do not reuse the
video bucket or book-figure bucket. Configure bucket CORS for only the actual
local/staging/production web origins and only the required methods/headers.

Replace the frontend's direct Supabase TUS upload with this flow:

1. Authenticated browser creates an ingestion job.
2. API reserves the immutable owner/job key and returns a short-lived presigned
   R2 `PUT` URL plus required headers.
3. Browser uploads with `XMLHttpRequest` so progress and abort still work.
4. Browser calls the existing completion endpoint.
5. API `HEAD`s the exact object, checks owner/job metadata, type, nonzero size,
   and the configured 50 MB limit, then queues the job.
6. Worker downloads through the storage abstraction and computes the existing
   source SHA-256 before parsing.

A single presigned PUT is acceptable under the current 50 MB cap. Preserve the
API's idempotency and immutable path. If the project later raises the cap above
100 MB, add S3 multipart upload rather than sending large objects through
FastAPI.

The presigned request must bind at least the exact bucket, key, expiry, and
`Content-Type: application/pdf`. Use a short expiry (for example, 10 minutes).
Do not place credentials or a broadly writable prefix in the browser.

Update source cleanup to paginate R2 listings and retain the existing:

- 24-hour abandoned-upload window;
- seven-day failed/cancelled retention;
- owner prefix validation;
- orphan grace period;
- maximum deletions per pass;
- authoritativeness/orphan-fraction guard;
- dry-run mode for first deployment.

### 2.5 Source-PDF migration command

Add an idempotent migration command modelled on
`scripts/migrate_book_images_to_object_storage.py`. It must support:

- `--dry-run`;
- `--verify-only`;
- `--owner <uuid>`;
- bounded batches and concurrency;
- explicit legacy source and R2 target configuration;
- resumability without an external checkpoint file;
- a nonzero exit code for any failed or unverified object.

Build the manifest from every distinct object named by:

- `books.source_storage_*`;
- `books.viewer_storage_*`;
- `ingestion_jobs.storage_*` that is still live under retention policy.

For each object:

1. Download from legacy Supabase or the verified local Storage recovery.
2. Check expected size and SHA-256 where the database has them.
3. Upload to R2 under the same owner-scoped key unless a documented collision
   forces a content-addressed key.
4. Read/head the R2 object and verify size, SHA-256 metadata, content type, and
   owner/job metadata.
5. In a short database transaction, change only the rows that name that exact
   object from backend `supabase` to `r2`.
6. Leave the legacy object untouched.

Never update a row before verification. A rerun must retry only unfinished
rows. A key collision with different content is a hard failure, not an
overwrite.

### 2.6 Database copy and validation command

Add a migration script that copies data from the frozen source into a
pre-migrated target without copying Supabase roles or service schemas.

It must:

- accept explicit source and target URLs without logging them;
- reject identical source and target endpoints;
- require PostgreSQL major-version compatibility;
- require the target to carry every repository migration;
- copy the minimal shadow-user columns before tenant data;
- copy all `public` and `video` table data in dependency-safe, bounded batches;
- either use deferred constraints or an explicitly audited table order;
- reset every identity/serial sequence;
- run `ANALYZE` after import;
- run exact counts, PK digests, canonical hashes, vector counts/dimensions,
  orphan checks, and source-object reference checks;
- be restartable against a disposable/empty target;
- refuse to merge into a nonempty target unless an explicit replacement mode
  is selected and the target identity has been triple-checked.

Do not use `pg_dump` ownership restoration from Supabase. The historic dump can
contain `supabase_admin` ownership and `SET ROLE` statements that an ordinary
target cannot reproduce reliably.

## Phase 3: automated tests and local rehearsal

Add or update tests for:

- Supabase JWT acceptance and rejection after variable renaming;
- signing-key rotation/cache refresh;
- shadow-user first request, repeat request, concurrency, and collision;
- R2 presigned PUT response contract;
- upload progress, abort, retry, and completion in the frontend;
- R2 head/download/delete/list/sign behavior with a fake S3 client;
- source backend dual-read during migration;
- source migration idempotency, partial failure, collision, and verify-only;
- book viewer and deck extraction using R2-signed PDFs;
- cleanup dry-run and authoritativeness refusal;
- generic Postgres bootstrap from empty PostgreSQL 17;
- all migrations from empty to head;
- exact and hybrid retrieval with `halfvec` on the generic target;
- cross-user API isolation.

Required local gates:

```bash
uv run python -m unittest discover -s tests -v
cd frontend && npm test
cd frontend && npm run typecheck
cd frontend && npm run lint:tokens
```

Also run the repository's relevant retrieval gold sets and compare against the
committed halfvec measurements. Infrastructure migration must not change
retrieval ranking or grounded-answer behavior.

Perform a full destructive rehearsal only against a named disposable local or
staging target:

1. Bootstrap empty generic Postgres 17.
2. Import the frozen source.
3. Point API-only local runtime at it; do not start cleanup workers yet.
4. Point Auth at a nonproduction Auth project.
5. Point media at nonproduction or read-only verified buckets.
6. Run the complete acceptance matrix.
7. Drop and repeat the import to prove determinism.

Record import duration, maximum disk use, dump size, and restore duration.

## Phase 4: provision approved external resources

### 4.1 Railway pgvector

After approval, add a PostgreSQL 17 service that includes pgvector to the
existing Railway project. The standard Railway Postgres template may not ship
pgvector; use Railway's pgvector template or a pinned, inspectable image.

- Keep the database private by default.
- Use the internal reference variable from the API:
  `DATABASE_URL=${{<postgres-service>.DATABASE_URL}}`.
- Enable public TCP only temporarily for the operator import/restore, then
  remove it.
- Start with the Hobby-plan 5 GB volume ceiling; alert at 60%, 75%, and 85%.
- Check total Railway projected usage before provisioning and preserve the
  user's hard monthly cap. Railway's $5 Hobby amount is a minimum/included
  credit, not a guarantee that the whole project costs $5.
- Create separate migration/operator and runtime roles. The API gets only the
  runtime URL; the migration URL stays outside the runtime service.
- Verify pgvector supports `halfvec` before importing.

Do not assume the database template is fully managed. This project owns its
backup, restore, extension, and upgrade procedures.

### 4.2 Supabase Auth-only project

After approval, create a new Free organization and one project. Do not create
it inside the restricted organization.

Configure:

- production Site URL and explicit redirect allow-list;
- email/password behavior matching the current product decision;
- custom SMTP if email confirmation/password reset must be reliable beyond the
  built-in free email limits;
- asymmetric JWT signing and published JWKS;
- no application migrations, Storage buckets, Edge Functions, or public data
  tables.

Record the Free project's inactivity-pause behavior as an accepted operational
limitation. Verify Auth availability before a portfolio demo, but do not add a
synthetic keepalive merely to evade the Free-plan policy. If unattended
always-on authentication becomes a requirement, stop and revisit the provider
decision rather than hiding that requirement in a scheduled ping.

Record project reference and public URL in the secret manager/environment, not
in committed prose. Keep the new service-role key out of the application; it is
needed only for controlled Auth administration during migration.

### 4.3 R2 book-source bucket

After approval, create or verify a private production bucket dedicated to book
source/viewer PDFs. Use a token scoped only to required R2 buckets and actions.
Configure CORS before browser testing. Do not enable an `r2.dev` public URL.

## Phase 5: migrate and verify object storage

Migrate book figures first if their current production bucket has not been
fully populated. Use the existing image migration command, then run an added
full verification mode over all 4,526 references. Set production
`BOOK_IMAGE_BACKEND=r2` and the distinct book-image bucket variables only after
the deployed API knows how to read object-backed figures.

Then run the new source-PDF migration command:

1. Dry run and save the manifest/count/byte total.
2. Copy one noncritical PDF and verify it end to end.
3. Copy all remaining source and viewer objects.
4. Run verify-only over the complete R2-backed set.
5. Confirm zero rows still need Supabase Storage, excluding deliberately
   retained failed/cancelled jobs documented by the report.
6. Keep all legacy Supabase objects intact through the rollback window.

If any step requires restoring source PDFs *into Supabase Storage* — a
rehearsal, or a rollback — do not unpack the storage tar into the volume.
Supabase Storage keeps each object's content type and cache headers in
**extended attributes on the file**, and the tar does not preserve them. The
bytes land, rows can be inserted by hand, the row count and file count both
look correct, and every read then fails with HTTP 500 and `ENODATA`. Restore
by uploading through the Storage API, which makes the service write the
metadata it will later look for:

```bash
python -m scripts.restore_book_sources <extracted-archive-root>
```

Before enabling cleanup, compare:

- database-referenced key count;
- R2 key count under every owner;
- referenced-but-missing count (must be zero);
- same-key/different-hash count (must be zero);
- orphan count and bytes;
- newest orphan age.

Cleanup remains dry-run until the migrated database is authoritative and the
first production observation window has passed.

## Phase 6: migrate Auth

The preferred path is an Auth-schema migration from the old hosted Supabase
project to the new Auth-only project. Supabase documents that this preserves
user IDs and password hashes.

1. Export only the Auth schema/data with PostgreSQL 17 tooling according to the
   current official Supabase migration procedure.
2. Inspect the archive TOC. It must not contain `public`, `video`, or `storage`
   application data.
3. Import into the empty Auth-only project with errors fatal.
4. Use the new project's signing secret/keys. Do not reuse the old JWT secret;
   invalidate old access/refresh tokens and require a fresh sign-in.
5. Remove or invalidate migrated sessions and refresh tokens if the supported
   migration procedure copied them.
6. Confirm every migrated Auth user ID that owns application data exists in the
   Railway shadow `auth.users` set.
7. Test password login, refresh, logout, reset flow, invalid token, wrong
   issuer, wrong audience, and signing-key refresh.

If the old Auth database cannot be read, use the Admin API to create the one
real account with its existing UUID and a generated temporary password, store
the password in macOS Keychain, and require the user to change it. Never invent
a new UUID. Record clearly that password continuity was not possible.

Do not switch production Auth variables yet. The new Auth project can be
tested against the candidate API/database before cutover.

## Phase 7: create and load Railway Postgres

1. Bootstrap the empty Railway target to repository head.
2. Confirm extension and `halfvec` compatibility.
3. Create least-privilege runtime and migration roles.
4. Take a named/manual empty-target backup if Railway supports it.
5. Load data from the frozen authoritative source using the provider migration
   command.
6. Reset sequences and run `ANALYZE`.
7. Run the full database audit.
8. Take an initial logical dump of the loaded target, checksum it, and restore
   it into a scratch database.

Acceptance SQL must include at least:

```sql
select current_setting('server_version');
select extname, extversion from pg_extension order by extname;
select pg_size_pretty(pg_database_size(current_database()));
select max(version) from supabase_migrations.schema_migrations;

select format('%I.%I', schemaname, relname), n_live_tup
from pg_stat_user_tables
order by 1;

select count(*) from public.image_blocks where storage_key is null;
select pg_typeof(embedding), dimension, count(*)
from public.chunk_embeddings group by 1, 2 order by 2;
select pg_typeof(embedding), embedding_kind, dimension, count(*)
from video.evidence_embeddings group by 1, 2, 3 order by 2, 3;
```

Use exact table counts and manifest digests for pass/fail; the statistics query
is diagnostic only.

## Phase 8: candidate-stack acceptance before cutover

Run the latest API locally or in isolated staging with:

- candidate Railway Postgres;
- new Supabase Auth project;
- R2 source, figure, and video buckets;
- workers disabled initially;
- all cleanup in dry-run.

Test:

1. Health and queue health.
2. Existing-user sign-in and refresh.
3. Books and papers list/open.
4. PDF signed URL and representative first/middle/last pages.
5. Figure rendering from several books and MIME types.
6. BM25, vector, hybrid, and reranked book questions.
7. Video/course list, playback references, evidence images, and grounded chat.
8. Deck generation/review, conversations, interviews, reading/watch sessions,
   and notifications.
9. New PDF upload to R2 and complete ingestion.
10. Worker restart/resume of a deliberately interrupted job.
11. Second-user isolation and guessed-ID 404 behavior.
12. Forged token, wrong issuer, expired token, and old-project token rejection.
13. Cleanup dry-run output against the authoritative target.

Only enable a worker after its target database and bucket inventories have been
shown to agree. Capture logs and LangSmith traces without secrets or private
document text.

## Phase 9: production cutover

Schedule a maintenance window and announce the expected downtime to the user.

### Pre-cutover gate

- repository is clean at the exact commit to deploy;
- all tests and candidate acceptance pass;
- target dump and restore drill pass;
- no active ingestion/interview operation remains;
- old and target manifests have no unexplained delta;
- Auth login is confirmed in the new project;
- all R2 object references verify;
- rollback variables and last healthy deployment IDs are recorded securely;
- Railway projected cost and hard limit are acceptable.

### Cutover order

1. Put the application into maintenance by scaling/stopping the production API
   and worker. Do not leave the old worker polling while data is copied.
2. Re-run the source reconciliation. If anything changed since the frozen
   snapshot, rebuild the target from a new final snapshot or apply a reviewed
   deterministic delta. Do not wave through drift.
3. Run final target database and R2 verification.
4. Stage API variables without printing values:
   - Railway internal `DATABASE_URL` reference;
   - no production `MIGRATION_DATABASE_URL` on the runtime;
   - new `AUTH_SUPABASE_URL`/issuer/audience;
   - R2 source, figure, and video backends/buckets/credentials;
   - `VIDEO_CLEANUP_DRY_RUN=1` and source cleanup dry-run;
   - correct CORS origin and LangSmith project.
5. Deploy the API/worker at the recorded commit with `scripts/deploy.sh api
   production`.
6. Wait for `/api/health` and `/api/health/queue`; confirm the reported build
   revision and Railway database readiness.
7. Stage frontend public Auth URL/key and API origin. These values are baked at
   build time, so deploy the web service with `scripts/deploy.sh web production`.
8. Confirm old-project tokens are rejected, then sign in freshly against the
   new Auth project.
9. Run the production smoke matrix below.
10. Re-enable the worker only after read paths pass and queue state is sane.
11. Keep cleanup dry-run through at least one complete scheduled sweep and
    inspect every proposed deletion.

### Production smoke matrix

- health and queue health;
- existing-user sign-in, refresh, logout, and re-login;
- exact expected book/paper/video/course counts;
- open three PDFs of different sizes and render representative pages;
- render at least ten figures across several documents;
- one exact-term BM25 question and one semantic/hybrid question with citations;
- one course question with evidence;
- open existing conversation, deck, interview, reading session, notification;
- upload a small disposable PDF, complete and process it, verify its R2 object,
  then remove it through supported application behavior;
- create a second user and prove cross-user 404/isolation;
- confirm no Supabase Storage calls in browser network logs;
- confirm API logs show Railway DB and R2 backends without exposing secrets.

## Phase 10: observation and rollback window

For at least seven days:

- keep old Supabase database/project and Storage objects unchanged;
- keep the frozen local dump and source manifests;
- monitor Railway database size, memory, CPU, connection count, slow queries,
  restart count, and spend;
- monitor Auth errors and JWKS refresh failures;
- monitor R2 missing objects, signing failures, and upload completion failures;
- keep cleanup in dry-run until one full output has been reviewed;
- take and verify daily logical dumps.

After the first clean cleanup report, enable deletion with the existing bounded
limits. Recheck the next run.

## Backup and restore plan

Enable three layers within the approved budget:

1. Railway daily volume backup with its standard short retention.
2. Railway weekly volume backup with one-month retention.
3. A portable PostgreSQL 17 custom-format logical dump uploaded to a private
   bucket outside the database volume.

PITR is desirable but must pass the cost-approval gate. At minimum, automate a
daily logical dump, retain seven daily and four weekly copies, record SHA-256,
and never put database URLs in logs.

Run a restore drill into a scratch database immediately after cutover and
monthly thereafter. Validate schema head, exact canonical counts, representative
hashes, vectors, Auth shadow rows, and application health. Record measured RPO
and RTO.

Remember: deleting a Railway volume also deletes its volume backups. The
off-provider logical dump is the disaster-recovery copy.

## Rollback

### Before any post-cutover write

If acceptance fails before the target receives new production writes:

1. Stop the candidate API/worker.
2. Restore the recorded old Auth/database/storage variables.
3. Redeploy the last healthy application image.
4. Verify old health and login.
5. Leave the failed target intact for diagnosis.

### After the target receives writes

There is no dual-write system. Do not point traffic back to an older database
and silently discard target-only rows.

1. Stop writes.
2. Export and inventory the target-only delta.
3. Prefer fixing forward on Railway.
4. If rollback is unavoidable, apply a reviewed delta to the old database only
   if it is writable and schema-compatible; otherwise obtain explicit user
   acceptance of the data loss window.
5. Preserve both databases and all object stores until reconciliation finishes.

Auth can be rolled back independently only if the API issuer and frontend Auth
configuration move together. Tokens from the wrong issuer must remain invalid.

## Retirement

After the rollback window and a successful fresh restore drill:

1. Export one final old Supabase Auth/metadata archive and Storage manifest.
2. Confirm Railway/R2 counts and hashes once more.
3. Remove obsolete `SUPABASE_SERVICE_ROLE_KEY` variables from API/worker.
4. Remove the temporary Railway database public TCP proxy.
5. Remove the compatibility fallback to old Auth variable names in a later
   release.
6. With explicit approval, pause or delete the old Supabase project/org.
7. Do not delete the off-provider migration archive under the normal cleanup
   policy; give it an explicit retention decision.
8. Update the architecture diagram and engineering log with costs, migration
   duration, failures, measured database size, RPO/RTO, and the reason vectors
   stayed in Postgres.

## Files expected to change

Exact names may vary after inspection, but the implementation should cover:

- `api/auth.py`
- `api/ingestions.py`
- `frontend/lib/supabase.ts` (Auth only after migration)
- `frontend/components/upload-panel.tsx`
- `ingestion/storage_objects.py` or its provider-neutral replacement
- `ingestion/cleanup.py`
- `ingestion/pipeline.py`
- `decks/extraction.py`
- `api/main.py` PDF signing path
- `storage/database.py`
- `storage/book_images.py`
- new generic Postgres bootstrap/migration scripts under `ops/` and `scripts/`
- new SQL migration(s) for storage backends and database-role/RLS policy
- `.env.example` and `frontend/.env.example`
- `docs/deployment.md`, `docs/environments.md`, `docs/architecture.md`, and
  `README.md`
- backend, frontend, migration, storage, and multi-user tests

Do not edit historic migration files that have already run. Add new timestamped
migrations.

## Final handoff report

Return a concise report containing:

- exact commit and deployed `BUILD_REVISION`;
- source and target database sizes;
- exact table/object/vector counts and digest comparison result;
- Auth users migrated and whether passwords were preserved;
- source PDFs/figures/videos copied, bytes, missing/collision/orphan counts;
- tests and evaluation results;
- production smoke results;
- backup schedules and restore-drill RPO/RTO;
- current Railway/R2/Supabase projected monthly cost;
- cleanup state (dry-run or enabled);
- rollback deadline and exact retained artifacts;
- any accepted data gap or remaining limitation.

Do not claim completion while any definition-of-done item is unverified.

## Current official references to recheck at execution time

- Railway PostgreSQL: <https://docs.railway.com/databases/postgresql>
- Railway pgvector guide: <https://docs.railway.com/guides/embeddings-pipeline>
- Railway pricing: <https://docs.railway.com/pricing>
- Railway Postgres backup/restore: <https://docs.railway.com/guides/postgres-backups-restores>
- Railway volume limits: <https://docs.railway.com/volumes/reference>
- Supabase Auth migration: <https://supabase.com/docs/guides/troubleshooting/migrating-auth-users-between-projects>
- Supabase billing/fair-use restrictions: <https://supabase.com/docs/guides/platform/billing-faq>
- Cloudflare R2 presigned URLs: <https://developers.cloudflare.com/r2/api/s3/presigned-urls/>
- Cloudflare R2 uploads: <https://developers.cloudflare.com/r2/objects/upload-objects/>
- Cloudflare R2 pricing: <https://developers.cloudflare.com/r2/pricing/>
