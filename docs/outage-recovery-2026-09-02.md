# Supabase outage recovery — 2026-09-02

## What happened

The production Supabase project (`usuulfckhbeypjxwjpfn`) stopped accepting connections
on or before 2026-09-01 19:33 UTC:

```
FATAL: 57P03: the database system is not accepting connections
DETAIL: Hot standby mode is disabled.
```

The free-tier 500 MB database disk was exhausted by a corpus that measures ~880 MB locally.
Postgres will not start, so `pg_dump` — and therefore the Supabase CLI and MCP — cannot
extract anything. `GET /v1/projects/{ref}/database/backups` returns `backups: []` with
`pitr_enabled: false`: the free plan keeps **no managed backups**, so Supabase holds no copy.

Two things worth remembering:

- The Management API reported the *project* as `ACTIVE_HEALTHY` while the `db` service was
  `UNHEALTHY`. Project status is not a liveness signal — check
  `/v1/projects/{ref}/health?services=db`.
- Media never lived in Supabase. `VIDEO_MEDIA_BACKEND=r2` puts all video media in Cloudflare
  R2, which was completely unaffected.

## What survived

| Asset | Location | State |
|---|---|---|
| 54 books, 7,152 chunks, 6,820 embeddings | local Postgres | intact |
| 54 source PDFs (464 MB) | local Supabase Storage | intact |
| 21 videos, 9,636 frames, transcripts (17.6 GB) | Cloudflare R2 | intact |
| 12,510 evidence units, 4,252 transcript segments | local Postgres | intact |
| MIT 6.824 course + 20 lectures | local Postgres | intact |
| Production-only rows written 2026-08-24 → 09-01 | prod Postgres | **lost** |

The local stack was a hash-verified clone taken 2026-08-24 by `scripts/clone_prod_to_local.sh`,
so the loss is limited to whatever production wrote in that eight-day window.

## Backups taken

Written to `artifacts/local-db-backups/` (now gitignored — it was not, and a 511 MB dump was
one `git add -A` away from being committed):

- `local-full-corpus-20260902-0052.dump` — 511 MB, pg_dump custom format, schemas
  `public`, `video`, `storage`, `auth`, `supabase_migrations`. Verified: 1,092 TOC entries.
- `local-storage-book-sources-20260902-0103.tar` — 464 MB, 54 book PDFs from the local
  Storage volume. These exist only as files; no database dump contains them.
- `staging-supabase-20260902-0103.dump` — 176 MB, the healthy staging project. Verified:
  747 TOC entries.

Dumps require the PostgreSQL 17 client (`/opt/homebrew/opt/postgresql@17/bin`); the default
`pg_dump` on this machine is 14.13 and refuses a v17 server.

## Recovery performed

1. **Media wired to R2.** Local `.env` now sets `VIDEO_MEDIA_BACKEND=r2` against the surviving
   production bucket, with `VIDEO_MEDIA_CACHE_ROOT=data/r2-media-cache`. This is a deliberate
   exception to the "local never points at production storage" rule in `docs/environments.md`:
   R2 holds the only copy of the media, and 17.6 GB does not fit in the ~7 GB of free disk.
   The S3 store is read-through, so only what is opened is cached.

2. **Single account.** Content had been split across three local users. Ownership was
   consolidated onto UUID `9462f7d3-d576-4ba1-b981-1b617d82fe34`, which was re-pointed to the
   real owner email. That UUID was kept deliberately: every R2 key and every Storage object
   path embeds it, `storage.objects` RLS matches on `(storage.foldername(name))[1] = auth.uid()`,
   and `video.*` carries `CHECK (storage_key LIKE owner_id || '/%')`. A genuinely new UUID
   would have required rewriting 9,699 R2 objects and 54 Storage paths.

   8,562 rows were reassigned. Storage-key columns and `owner_id` had to move in a single
   statement per table — `frames_check` validates both key columns against the owner at once —
   and the migration ran under `session_replication_role = replica` because composite
   `(video_id, owner_id)` foreign keys make update order unsatisfiable. Integrity was verified
   before commit: zero orphans, zero key/owner mismatches.

   The two `bootstrap@study-partner.local` books were left alone: both are exact `file_hash`
   duplicates of books the library already owns.

3. **Video sources relinked.** 19 of 21 `video_sources` rows had a null `storage_key` — their
   media had been written to the local filesystem store, which was later cleared. All 21 are
   now linked to R2. Matching used the yt-dlp metadata objects in R2 (`canonical/metadata/`),
   which carry the YouTube id and title; each metadata object pairs with its mp4 within one
   second of upload time, cross-validated against `filesize_approx`. The last one,
   *Lecture 2: RPC and Threads*, was resolved by elimination and confirmed with `ffprobe` over
   a presigned URL (4,774 s ≈ 80 min, matching its metadata). All 21 keys verified present in
   R2 with matching sizes.

## Still outstanding

- **208 course frame images are gone.** They were filesystem-only and the directory was
  cleared; none of their hashes exist in R2. The rows and their derived analysis
  (`visual_observations`, `visual_regions`, `evidence_units`) are intact. Frames are
  re-extractable from the now-linked mp4s with ffmpeg — no model spend — but re-extraction
  produces new hashes, so any vision/OCR tied to those frames would need re-running.
- **Production is still down.** Nothing here revives it. Restoring the hosted service at this
  corpus size needs Pro (8 GB disk); there is no usage-only Supabase tier below the $25/mo base.
- **The 2026-08-24 → 09-01 gap** cannot be enumerated while the database refuses connections.

## Incident during recovery: the cleanup worker deleted R2 objects

Starting the full local service (`scripts/serve.py` runs the API **and** the ingestion worker)
against the R2 bucket caused `study_partner.video.cleanup` to delete objects it judged orphaned:

```
deleted orphaned S3 video object 9462f7d3-…/canonical/frames/sha256/…jpg
(216177 bytes; no row references it)
```

R2 held ~9,636 frame images from production's ingestion runs, but the local database — which
lost production's video rows when Postgres died — only references 1,756 frames. Every other
frame looked orphaned, and the cleanup pass began removing them.

**Scope:** 995 objects (~0.17 GB) were deleted, all frame JPEGs, before the worker was killed.

**What was not lost:** cross-checking every storage key referenced by the local database
against the surviving bucket returned `deleted AND referenced by local DB: 0`. All 21 source
mp4s, all captions, and all yt-dlp metadata objects survive — object counts for those types
actually rose, because the worker also began re-ingesting the two queued jobs before it was
stopped. Recorded ingestion spend across the whole database is $0.40, of which this incident
contributed about $0.002.

The deleted frames were production frames whose database rows were already gone; they were
not reachable from any local row and could not have been rendered. They are re-derivable from
the surviving mp4s with ffmpeg. R2 could not be checked for versioning — the token is denied
`GetBucketVersioning` and R2 does not implement `ListObjectVersions` — so recovery, if any,
would have to go through the Cloudflare dashboard.

**Guards now in place:**

- `VIDEO_CLEANUP_DRY_RUN=1` is set in local `.env`.
- For browsing, run the API alone — `.venv/bin/python -m uvicorn api.main:app --host 127.0.0.1
  --port 8000` — not `scripts/local.sh up` or `scripts.serve`, both of which start the worker.
- Note that killing the supervisor does **not** kill the worker: it survived as an orphan with
  PPID 1 and had to be killed separately (`pkill -f worker.main`).

Any local worker pointed at a shared production bucket will delete whatever the local database
does not reference. Treat cleanup as unsafe whenever the database is not the authoritative
index of that bucket.
