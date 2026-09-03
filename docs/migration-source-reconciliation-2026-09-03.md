# Migration source reconciliation — 2026-09-03

Phase 1.1 of `docs/railway-postgres-auth-migration-runbook.md` requires that
the migration source be *established*, not assumed. It was measured, the two
candidates diverged, and the divergence was material enough to change the
answer. This records what was found, what was decided, and what the decision
costs.

## What the runbook assumed

> The local database is the presumptive migration source because it contains
> the latest size-reduction migrations and the recovered full corpus.

The first half is true. The second is not. The recovered local database is a
full corpus for **books** and a **partial** one for **video**.

## Measured divergence

Exact `count(*)` on all 55 `public`/`video` tables in both databases. Both are
PostgreSQL 17.6. Full listings are in the gitignored evidence directory as
`counts_local.txt`, `counts_prod.txt` and `counts_reconciliation.psv`.

Twenty-eight tables match exactly, including the entire book corpus:

| Table | Local | Hosted prod |
|---|---:|---:|
| `public.books` | 56 | 56 |
| `public.nodes` | 3,216 | 3,216 |
| `public.content_blocks` | 91,736 | 91,736 |
| `public.chunks` | 7,152 | 7,152 |
| `public.chunk_sources` | 90,231 | 90,231 |
| `public.chunk_embeddings` | 6,820 | 6,820 |
| `public.image_blocks` | 4,526 | 4,526 |
| `public.image_captions` | 4,400 | 4,400 |
| `public.table_blocks` | 721 | 721 |
| `public.ingestion_jobs` | 59 | 59 |
| `video.videos` / `video.video_sources` | 21 / 21 | 21 / 21 |

Twenty-seven diverge. The video derived tables are not close:

| Table | Local | Hosted prod | Prod surplus |
|---|---:|---:|---:|
| `video.evidence_units` | 12,510 | 158,695 | +146,185 |
| `video.evidence_embeddings` | 11,295 | 123,514 | +112,219 |
| `video.transcript_segments` | 4,252 | 38,228 | +33,976 |
| `video.frames` | 1,756 | 10,621 | +8,865 |
| `video.visual_observations` | 1,732 | 9,532 | +7,800 |
| `video.visual_regions` | 1,452 | 5,515 | +4,063 |
| `video.visual_events` | 562 | 3,426 | +2,864 |
| `video.chapters` | 38 | 271 | +233 |
| `video.transcript_sources` | 2 | 21 | +19 |
| **Total, diverged video tables** | **34,086** | **354,214** | **+320,128** |

The canonical video rows match (21 videos, 21 sources); only two lectures have
their derived data locally. Prod also holds more recent user activity: decks
(23 vs 22), deck cards (339 vs 324), deck topics (481 vs 452), interview
sessions (23 vs 21), interview turns (82 vs 76) and notifications (12 vs 4).

Local's only surplus is 14 conversations and their turns, all created
2026-09-01 → 2026-09-03 under the primary owner, with titles like
`summarize chapter 3` and `explain chapter 4`. These are this workstation's
own testing, not production activity.

**Neither database is a superset. Prod has no gap; local has a 92% video gap.**

## Why local is nevertheless three migrations ahead

Hosted prod is at `20260829130000`; local is at `20260903170000`. Prod still
has `image_blocks.base64_content`, `vector` rather than `halfvec` embeddings,
and video text vectors at 3,072 dimensions.

All three missing migrations are deterministic SQL that buy no model calls —
the halfvec one truncates Matryoshka-style in place. Prod could therefore have
been migrated forward without re-embedding anything, and the two databases'
4,526 `image_blocks` were verified byte-identical: same `block_id` set, and
sampled rows' recorded local `base64_hash`/`size_bytes` reproduce exactly when
`sha256(base64_content)` is recomputed on prod. The local backfill's
`storage_key`/`content_hash` columns are transplantable rather than needing a
re-upload; the 4,342 deduplicated objects are already in the R2 book-media
bucket.

## Decision

**The local database is the migration source, by explicit instruction after
the divergence and its cost were presented.** The production account is
`human@rockfortrobotics.com`, which is the email the local shadow `auth.users`
row carries for the preserved primary owner UUID
`9462f7d3-d576-4ba1-b981-1b617d82fe34`. Hosted prod Auth carries
`rabhishek100@gmail.com` on that same UUID; the UUID is preserved, the email
is not.

Accepted consequence: the migrated production database will contain derived
video data for two of twenty-one lectures. Course and lecture canonical rows
all migrate, so the library lists correctly, but grounded video answers,
evidence images and chapter navigation will only work for the two lectures
whose evidence survived into the local clone.

## What keeps this reversible

The hosted Supabase project is not deleted, paused or written to. The 320,128
prod-only video rows remain intact and readable there for the full rollback
window, so this is a deferral rather than a destruction:

- The prod video rows are `video.*` derived data hanging off canonical rows
  whose primary keys migrate unchanged, so a later backfill is an insert into
  a target that already has the parents.
- Re-deriving them from media instead would mean re-running transcription,
  vision and embedding over nineteen lectures.
- The one transformation needed is the `20260903170000` conversion, which is
  pure SQL and can be applied to the extracted rows before insert.

A backfill command for this is **not** part of this cutover. It is recorded
here as the known follow-up, and the retirement step in the runbook must not
delete the old Supabase project until it has either run or been waived.

## Also recorded at capture time

- Hosted prod Supabase is alive and readable: 17.6, 1,571 MB, a full scan of
  `video.evidence_embeddings` (123,514 rows) completes over the pooler. The
  production API is nevertheless returning 503 with
  `canonical_database_ready: false` on build `249f62d-dirty`.
- Both databases reference the same 54 source PDFs, and both the hosted and
  the local `book-sources` bucket hold 54 objects totalling 464 MB. The local
  Storage recovery is complete, so source PDFs can be read locally.
- R2 `agentic-study-partner-video-media-prod`: 8,728 objects, 17.44 GB.
- R2 `agentic-study-partner-book-media-prod`: 4,342 objects, 354 MB.
- No R2 bucket for book source PDFs exists yet.
- Production API/worker carry no `BOOK_IMAGE_*` variables.
- Local `video.ingestion_jobs` holds 3 `queued` and 14 `retry_scheduled` rows
  that reference lectures whose derived data is not in the local clone. They
  must not be handed to a worker after cutover.
