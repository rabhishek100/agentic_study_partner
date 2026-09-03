# Migration readiness: what actually depends on Supabase

Written 2026-09-02, from the recovered local environment rather than from the
schema on paper. The production database became unavailable at roughly 500 MB
on the free plan, and Pro's $25/month base charge is not wanted, so the
question is what it would take to run this on a cheaper Postgres.

The short answer: **the data is ordinary Postgres, and the two things that are
not are Auth and Storage.** Neither is deeply entangled, and the database is
about half the size it needs to be.

## What is standard Postgres

Every table the product reads and writes — books, papers, nodes, content
blocks, chunks, embeddings, videos, courses, lectures, transcripts, frames,
evidence, jobs and checkpoints — is plain Postgres with plain constraints. A
`pg_dump` restores them into any Postgres 17 with pgvector.

One caveat found the hard way during this recovery: **the dump is not
self-sufficient.** Its vector columns are declared `extensions.vector(3072)`,
and the dump contains neither the `extensions` schema nor the extension. A
restore into an empty database therefore fails on exactly two tables —
`public.chunk_embeddings` and `video.evidence_embeddings` — and cascades into
26 errors that are easy to skim past, leaving a database that looks restored
and has lost every embedding. Creating the schema and extension first reduces
that to one benign "schema public already exists".

Restoring also needs `supabase_admin`, not `postgres`: `postgres` is not a
superuser on this stack and cannot `SET ROLE`, so an ownership-preserving
restore run as `postgres` silently drops ownership.

Extensions actually in use: `vector`, `pgcrypto`, `uuid-ossp`,
`pg_stat_statements`, `pg_net`, `supabase_vault`. Only the first three are the
product's; a target provider needs pgvector and the two utility extensions.

## What must move for Auth

`auth.users` is one table with three rows. The coupling is not the table, it
is the function: **all 58 row-level-security policies are written as
`owner_id = (select auth.uid())`**, and `auth.uid()` is a Supabase function
that reads a claim out of the request JWT.

What makes this cheap rather than expensive is that **the application does not
depend on RLS for correctness.** Ownership is enforced in the queries
themselves — `video/repository.py` alone carries 40 explicit `owner_id = %s`
filters — and the API verifies the token itself (`api/auth.py`) and derives
the owner from the verified subject. RLS is defence in depth against direct
client access, not the mechanism the product relies on.

So a migration needs:

- an identity provider that issues JWTs the API can verify (it already
  supports both asymmetric JWKS and a shared secret), and
- either a compatible `auth.uid()` shim reading the same claim, or dropping
  the policies if no untrusted client ever connects to the database directly.

The second is the honest option for this product: nothing but the API and the
worker connect to Postgres.

## What must move for Storage

54 book and paper source PDFs, 464.7 MB, in one bucket. The rows in
`storage.objects` are Supabase's own schema, and the service keeps each
object's content type and cache headers **in extended attributes on the file**.

That last detail is the trap, and this recovery hit it: unpacking the storage
archive into the volume restores the bytes and not the xattrs, so every object
then fails to read with a 500 and `ENODATA`, while the row count and the file
count both look perfect. The working procedure is to upload through the
Storage API and let it write its own metadata — `scripts/restore_book_sources.py`.

Since video media already moved to Cloudflare R2 behind the `MediaStore`
protocol, the same treatment for book sources would remove Supabase Storage
entirely. That is the natural next step and it is mostly already built.

## Expected database size after cleanup

Current: **851 MB**. The distribution says where to cut:

| Table | Size | Share |
|---|---|---|
| `public.image_blocks` | **446 MB** | **52%** |
| `video.evidence_embeddings` | 136 MB | 16% |
| `public.chunk_embeddings` | 97 MB | 11% |
| `public.chunks` | 40 MB | 5% |
| `public.content_blocks` | 37 MB | 4% |
| everything else | ~95 MB | 11% |

`image_blocks` holds **4,526 rows of base64-encoded image bytes** in a text
column. Base64 costs about a third again over binary, and none of it needs to
be in Postgres: these are derived render artifacts of book pages, addressable
by key like every other media object.

**Moving `image_blocks` to object storage takes the database from 851 MB to
roughly 425 MB** — under the free-tier limit that caused the outage, without
touching a single row of canonical content. That is the single highest-value
change available, and it is the same move already made for video media.

Embeddings are the next 27%, and they are derived and rebuildable, but they
are also what makes semantic retrieval work; they should stay.

## What a cheaper provider has to offer

1. **Postgres 17 with pgvector.** Non-negotiable: 3072-dimension vectors in
   two tables.
2. **A direct connection for migrations** and a pooled one for the API. The
   deployment already separates `MIGRATION_DATABASE_URL` from `DATABASE_URL`.
3. **Enough room for ~425 MB after the image cleanup**, with headroom to grow
   as the remaining 19 lectures are ingested (estimated +350 MB of embeddings
   and evidence at full course ingestion, so plan for ~1 GB).
4. **Real backups.** The outage was survivable only because a local dump
   existed; the free plan had no managed backups. This is worth paying for
   before anything else on this list.

Neon fits on all four. What it does not provide is Auth or Storage, which is
why those two sections above matter more than the database choice.

## Order of work

1. Move `image_blocks` to object storage. Halves the database, needs no new
   provider, and is reversible.
2. Move book sources from Supabase Storage to R2, reusing the `MediaStore`
   contract video already uses.
3. Replace `auth.uid()` policies, or drop them once nothing but the API
   connects.
4. Only then move the database itself.

Steps 1 and 2 are worth doing regardless of whether the provider ever changes.
