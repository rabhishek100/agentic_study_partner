# Moving off the dead Supabase project: Railway for data, a new Supabase for Auth

*Written 2026-09-03 for an agent picking this up with no memory of the work that
produced it. Everything asserted here was measured on the local corpus on that
date; re-measure rather than trust anything that looks stale.*

## Why this exists

The production Supabase project became unavailable at roughly 500 MB on the
free plan. Supabase Pro is $25/month for capacity this project does not need,
so the data moves to Railway Postgres — where the API and worker already run —
and Auth moves to a **new, separate Supabase project** used for nothing but
authentication.

This splits one database into two. That is the whole difficulty, and §4 is
where it bites.

## 0. State of the world

The local corpus at `postgresql://…@127.0.0.1:54322/postgres` is the source of
truth. It was recovered on 2026-09-02 and is complete:

| | |
|---|---|
| database size | **266 MB** |
| books + papers | 56 (13 books, 41 papers, 2 owned by a stale account) |
| `auth.users` | 3 |
| book source PDFs (Supabase Storage) | 54 |
| videos / course lectures | 21 / 20 (MIT 6.824, 2 of 21 fully ready) |
| figures | 4,526 — **already in R2**, not in the database |

Media already outside Postgres:

- `agentic-study-partner-video-media-prod` — 17.4 GB, video/frames/regions
- `agentic-study-partner-book-media-prod` — 0.35 GB, the 4,526 figures

Still inside Supabase: **the 54 book source PDFs**, in Supabase Storage.

Backups are in `~/StudyPartnerRecovery/`, checksummed. `GOLDEN-v2-…` and
`pre-halfvec-…` are the useful ones. **Take a fresh dump before starting and
do not delete any of them until §8 passes.**

## 1. Tooling traps that will cost you an hour each

**Use PostgreSQL 17 binaries explicitly.** The `pg_restore` on `PATH` is
14.13 and rejects these dumps outright with `unsupported version (1.16) in
file header`. Every command below assumes:

```bash
PG17=/opt/homebrew/opt/postgresql@17/bin
```

**The dump is not self-sufficient.** Vector columns are declared
`extensions.vector`, and the dump contains neither the `extensions` schema nor
the extension — Supabase's platform provides them. Restoring into an empty
database fails on `public.chunk_embeddings` and `video.evidence_embeddings`
and cascades into ~26 errors that are easy to skim past, leaving a database
that looks restored and has lost **every embedding**. Always:

```sql
create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;
```

**`postgres` is not a superuser on the Supabase stack**; `supabase_admin` is.
An ownership-preserving restore run as `postgres` silently drops ownership.

**Never run the test suite against the corpus.** It claims real ingestion jobs
and, worse, deletes book-source PDFs through the Storage API — the Storage
service serves one database regardless of what `DATABASE_URL` says. Tests run
against a separate database (`study_partner_test`). If sources are lost,
restore with `scripts/restore_book_sources.py` (never by unpacking the tar —
see §3).

## 2. Human actions that must happen first

An agent cannot do these. Stop and ask.

1. **Railway**: a Postgres service in the same project/region as `api` and
   `worker`. Railway ships PostgreSQL 18.4 with pgvector 0.8.6, which is
   ahead of local (17.6 / 0.8.2) and supports everything used here.
2. **Railway backups**: enable them. They are *not* on by default. Daily kept
   6 days, weekly 1 month, monthly 3 months.
3. **A new Supabase project**, free tier, for Auth only. Note its project URL,
   anon key, service-role key, and JWT secret.
4. **Credentials into `.env`** (gitignored) — never pasted into a chat:
   `RAILWAY_DATABASE_URL`, and the new `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`.

## 3. Decide the fate of the 54 book PDFs

They are the last thing in Supabase Storage. Three options:

- **(a) Move them to R2** — recommended. Figures already live there, the
  bucket exists, and it removes Supabase Storage from the runtime entirely.
  Costs a new code path: book *sources* go through the Supabase Storage HTTP
  API today, which is not the `MediaStore` contract the figures use.
- **(b) Keep them on the new Supabase project.** Cheapest to build, but keeps
  a second Supabase dependency alive and its 1 GB free storage cap in play.
- **(c) Leave them behind.** Not viable — the reading pane has nothing to open.

**Whichever is chosen, the trap is the same:** Supabase Storage keeps each
object's content type in **extended attributes on the file**. The tar backup
does not preserve xattrs, so unpacking it into the volume restores files the
service cannot describe — every read then fails with HTTP 500 and `ENODATA`,
while the row count and file count both look perfect. Restore by *uploading
through the API*: `python -m scripts.restore_book_sources.py <extracted-root>`.

## 4. The hard part: splitting auth from data

Today `auth.users` and every app table live in one database. After the split
they do not, and two things break.

### 4a. Twelve foreign keys cannot survive

These reference `auth.users(id) ON DELETE CASCADE` and become impossible:

```
public: books, conversations, deck_jobs, deck_preferences, decks,
        ingestion_jobs, interview_sessions, notifications, study_preferences
video:  courses, resources, videos
```

They must be dropped, and `owner_id` becomes an unenforced UUID. **Understand
what is lost:** deleting a user no longer cascades their content. If account
deletion is ever offered, it needs an explicit application-level cascade.
Write that down in the migration rather than discovering it later.

(Eight further FKs — `identities`, `sessions`, `mfa_factors` and so on — are
Supabase-internal and travel with the auth database. Leave them alone.)

### 4b. Fifty-four RLS policies call a function that will not exist

28 in `video`, 26 in `public`, all of the form
`owner_id = (select auth.uid())`. `auth.uid()` is Supabase's, reading a claim
from the request JWT. Railway Postgres has no such function.

**The application does not rely on these for correctness.** Ownership is
enforced in the queries themselves — `video/repository.py` alone carries 40
explicit `owner_id = %s` filters — and the API verifies the token and derives
the owner from its subject (`api/auth.py`). RLS is defence in depth against
direct client access, and nothing but the API and worker connects to this
database.

Two defensible choices:

- **Drop the policies.** Honest, simple, and matches how the system actually
  works. Requires confirming nothing else connects — check for PostgREST,
  Supabase client usage in the frontend, and any external tooling.
- **Port a shim**: define `auth.uid()` reading a session GUC the API sets per
  request. Keeps defence in depth, adds a per-request `set_config` and a real
  chance of a subtle mismatch.

Recommend the first, but **make it an explicit decision with the user, not a
default.** Deleting 54 security policies is not a step to take quietly.

### 4c. What the API needs to accept the new issuer

`api/auth.py` already verifies both asymmetric (JWKS at
`{issuer}/.well-known/jwks.json`) and symmetric (HS256 shared secret) tokens,
so no code change is required — only configuration:

```
SUPABASE_URL=<new project url>
SUPABASE_JWT_SECRET=<new project jwt secret>     # if symmetric
SUPABASE_JWT_ISSUER=<defaults to {SUPABASE_URL}/auth/v1>
SUPABASE_JWT_AUDIENCE=<defaults to "authenticated">
```

### 4d. The three users must keep their UUIDs

Every `owner_id` in the corpus points at
`9462f7d3-d576-4ba1-b981-1b617d82fe34` (`human@rockfortrobotics.com`).
**If the new Supabase project issues a different UUID for that account, every
row in the corpus is orphaned.** Either create the user with the same id
through the Admin API, or rewrite `owner_id` everywhere — the former is far
safer. Verify before migrating data, not after.

## 5. Migration, in order, with a gate after each step

Do not proceed past a failing gate.

1. **Fresh dump** of the corpus. Verify with `pg_restore --list` and record its
   sha256 alongside.
2. **Provision** Railway Postgres. Confirm `select extversion from pg_extension
   where extname='vector'` returns ≥ 0.7 — `halfvec` depends on it.
3. **Create** `extensions` + `vector` + `pgcrypto` (see §1).
4. **Apply migrations** to the empty Railway database, in filename order.
   *Gate:* all migrations apply with zero failures. This is verified locally
   today (49 of 49), so a failure means an environment difference worth
   understanding before continuing.
5. **Drop the 12 auth FKs and decide the RLS question** (§4a, §4b) as a
   migration in the repo — not as ad-hoc SQL against the new database.
6. **Restore data only** (`--data-only`), excluding `auth`, `storage`,
   `realtime`, `vault`, `supabase_migrations`. *Gate:* row counts match §0
   exactly, table by table.
7. **Create the user** in the new Supabase project with the id from §4d.
   *Gate:* the id matches byte for byte.
8. **Point `.env` and Railway variables** at both new services. Deploy is
   CLI-driven: `railway up --service api`, then `worker`, then `app`. Merging
   to `main` deploys nothing.

## 6. Backups, before you call it done

Railway snapshots live inside Railway. The 2026-09-02 outage was survived only
because a dump existed **outside** the provider. Add a scheduled `pg_dump` to
R2 — one dump is currently 166 MB; 30 days' retention is ~5 GB and about
$0.07/month, since R2 is already past its 10 GB free tier at 17.8 GB.

Verify by restoring one, not by observing that the file exists.

## 7. What must not happen

- Do not run the test suite against the migrated corpus (§1).
- Do not start a worker before confirming `VIDEO_CLEANUP_DRY_RUN` is unset or
  `1`. Both retention sweeps default to reporting since 2026-09-02, and both
  refuse a database that cannot account for the storage it is sweeping — but
  do not lean on the guard.
- Do not delete any backup in `~/StudyPartnerRecovery/` until §8 passes.
- Do not `railway up` a service whose environment variables have not been
  updated; a half-migrated deployment writing to the old database is worse
  than a broken one.

## 8. Definition of done

- Row counts on Railway match §0 exactly.
- `human@rockfortrobotics.com` signs in against the new Supabase project and
  sees 13 books, 41 papers, 21 videos and the MIT 6.824 course.
- A book question, a video question and a course question each return a
  grounded answer with citations that resolve — the course answer naming the
  right lecture and timestamp.
- A book PDF opens in the reading pane (proves §3 end to end).
- A figure renders (proves R2 + the `image_blocks` storage keys survived).
- Full backend suite passes against a *separate* database.
- One backup has been restored and verified, not merely written.
- The old Supabase project is still untouched, so rollback remains possible.

## 9. Rollback

Nothing here is destructive to the source: the local corpus and the dumps are
untouched throughout. If the migration fails, point `.env` back at the local
stack and redeploy. Keep the old Supabase project alive until §8 has passed
and the system has run for a few days.

## Appendix: things measured on 2026-09-03

Useful for sanity-checking that the migrated database behaves like the source.

- Embeddings are `halfvec`; video text vectors are 1024 dimensions, book
  vectors remain 3072. This was measured, not assumed: `halfvec` costs nothing
  (recall identical on both gold sets), and truncating to 1024 costs 1.6% on
  video and 4.5% on books, which is why only video took it. Re-run with
  `python -m scripts.measure_embedding_encoding --dimension 1024`.
- There is **no ANN index**. `vector(3072)` could not have one (pgvector caps
  HNSW at 2000 dimensions); `halfvec` now could, but the retrieval SQL casts
  to `::extensions.vector` and wraps the scan in `distinct on`, so the planner
  still chooses a sequential scan. Available, unimplemented, unmeasured.
- Binary quantisation with rescoring recovered 98.2% of exact search at depth
  100, from an index 15× smaller. The reserve lever if the corpus outgrows
  the machine. Also unimplemented.
- Superseded ingestion versions accumulate on every re-ingest;
  `scripts/prune_superseded_versions.py` reclaims them and keeps versions that
  are current, cited by an answer, or targeted by a running job.
