# Railway Postgres cutover — 2026-09-03

Production's application database is Railway Postgres. This is what was done,
what was measured, and what is deliberately not finished yet.

The plan is `railway-postgres-auth-migration-runbook.md`; the source-of-truth
decision and its consequences are in
`migration-source-reconciliation-2026-09-03.md`. This is the outcome.

## What production is now

```text
Browser
  |-- sign-in/session ---------> Supabase project usuulfckhbeypjxwjpfn (Auth only)
  |-- application API --------> Railway api (FastAPI + ingestion worker)
  |                               |
  |                               |-- private DATABASE_URL, role app_runtime
  |                               v
  |                            Railway Postgres 18.6 + pgvector 0.8.6
  |
  `-- signed URLs ------------> Cloudflare R2 (figures, video) and, for now,
                                Supabase Storage (source PDFs)
```

| | Before | After |
|---|---|---|
| Application database | Supabase Free, 1,571 MB, **down** | Railway Postgres 18.6, 281 MB, healthy |
| Database role | `postgres` superuser | `app_runtime`, no DDL, no BYPASSRLS |
| API health | `503`, `canonical_database_ready: false` | `200 ok` |
| Build | `249f62d-dirty` | `53bd382` |
| Book figures | inline base64 / unconfigured in prod | R2 `book-media-prod`, 4,342 objects |
| Auth account | `rabhishek100@gmail.com` | `human@rockfortrobotics.com`, same UUID |
| Backups | none | 193 MB off-provider dump + verified drill |

## Verified in production

Against the live API at `53bd382`, signed in as the real owner:

- **health** `ok`, canonical and retrieval both ready; queue clean.
- **library** 13 books + 41 papers = 54, 1 video, 1 course of 20 lectures.
- **PDFs** three books of different sizes signed and fetched; all three return
  `%PDF` magic bytes over the signed URL.
- **figures** twelve figures across several books rendered from R2, each an
  exact byte-size match against the size recorded in the row.
- **retrieval** a BM25 question correctly reported insufficient corpus evidence
  and said so rather than inventing an answer; the same question under
  `hybrid_rerank` returned a grounded answer with three page-level citations,
  which exercises the `halfvec` vector path on Railway.
- **course chat** a grounded answer with three citations from the Spanner
  lecture.
- **isolation** a second account sees zero books, papers, videos, courses,
  conversations, decks and notifications, and gets `404` on the owner's book
  source, chapters and figures where the owner gets `200`.
- **auth rejection** `401` for no token, a malformed token, the anon key and
  the service-role key.
- **identity registry** the second account's `auth.users` row was created by
  `ensure_application_user` on its first authenticated request, with no
  foreign-key failure — confirmed by the restore drill reporting 4 identities.

## Numbers

- **Copied** 247,425 rows across 55 tables, 55/55 matching on exact count and
  order-independent primary-key digest, every foreign key re-proved by
  anti-join, in 11.8s locally and about 20 minutes over Railway's public proxy.
- **Determinism** two independent loads into a freshly rebuilt target produced
  identical per-table digests.
- **Audit** 15/15 on the loaded target: object inventory against a baseline
  built from a clean bootstrap, `extensions.halfvec` embedding columns, 3,072
  for books and 768/1,024 for video, all 4,526 figures naming a verified
  object, `base64_content` gone.
- **Backup** 193 MB custom-format dump in ~2.5 min; restore and verify into a
  scratch PostgreSQL 18 cluster in **9 seconds**. Measured RTO for the database
  is therefore minutes; RPO is the age of the last dump.
- **Tests** 1,748 passing.

## Deliberately not finished

**Source PDFs are still on Supabase Storage.** The R2 bucket
`agentic-study-partner-book-sources-prod` exists, and
`scripts/migrate_source_pdfs.py` is written and its manifest verified against
the live database — 54 distinct objects, 106 referring rows, 52 with a recorded
sha256, matching an independent export taken before any of this began. It is
blocked on an R2 API token whose scope includes the new bucket; the existing
token is scoped to the older buckets and returns 403. Until that runs,
`SUPABASE_SERVICE_ROLE_KEY` remains a runtime dependency and the frontend keeps
its Supabase Storage upload path — two definition-of-done items deferred, not
met.

**Auth is on the old project rather than a new one.** Supabase Free caps a user
at two active projects and the Management API cannot create organizations, so
the runbook's "new Free organization" was not reachable. The old project now
serves identities only; its application tables are dead weight and pruning them
is what will stop its disk filling again, which is what took production down on
2026-09-01. Do that before the rollback window closes, not after.

**Nineteen of twenty-one lectures have no derived video data**, by the
source-of-truth decision recorded in the reconciliation document. The rows
still exist in the old Supabase project and a backfill remains possible; the
old project must not be deleted until that has run or been waived.

**The public TCP proxy on the Postgres service is still enabled.** It is needed
for backups from a laptop and should be removed at retirement.

## Rollback

The frozen pre-cutover source is `artifacts/migration/frozen/source-full.dump`
(158 MB, checksummed) plus the 54 source PDFs exported with per-object sha256.
The old Supabase database is untouched and still holds everything.

Production has taken writes since cutover, so rolling the database back means
accepting the delta rather than pretending it does not exist: as of this
document the target holds 251,306 rows against the frozen source's 247,425.
Roughly 3,800 of those are one lecture the worker re-derived (see the
reconciliation document), and the rest are this session's smoke tests. Prefer
fixing forward.

Auth can be rolled back independently only by moving the API issuer and the
frontend configuration together.

## Operating it

```bash
# health
curl -s https://api-production-08e6b.up.railway.app/api/health | jq .

# audit the database's shape, not just its migration head
TARGET_DATABASE_URL=... uv run python -m scripts.audit_postgres_target

# off-provider backup, then prove it restores
BACKUP_DATABASE_URL=... scripts/backup_database.sh dump
DRILL_DATABASE_URL=... scripts/backup_database.sh drill

# what still needs to move to R2
DATABASE_URL=... uv run python -m scripts.migrate_source_pdfs --dry-run
```

Take a dump daily and drill monthly. Deleting a Railway volume deletes its
volume backups with it, which is why the off-provider copy is the one that
matters.
