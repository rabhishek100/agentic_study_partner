# Migration plan: SQLite/Chroma to Supabase Postgres

## Goal and temporary boundary

Move canonical content, lexical retrieval, and vectors into Supabase Postgres
while preserving the existing lossless/rebuildable data boundary.

The database schema is multi-tenant from the start, but the first cutover runs
in **single-user bootstrap mode** through a server-side `DEFAULT_OWNER_ID`.
Frontend authentication and cross-user security testing are a separate follow-up.
Until that follow-up is complete, this deployment must not be described or
exposed as a secure multi-user application.

## Implementation status (2026-07-23)

Stages 1–5 are implemented and validated against local Supabase. The hosted
Supabase project also has the database-only schema, bootstrap owner, canonical
book, and lexical index. The hosted restore matched losslessly with 177 nodes,
4,200 content blocks, 32 tables, 119 images, and 15,721,716 image-payload
characters. Rebuilding hosted chunks twice left one active build with 332
chunks. Hosted pgvector contains one provenance-checked 3,072-dimensional
embedding for every chunk; a second build reused all 332 rows. Direct hosted
BM25, vector, and hybrid queries returned the expected distribution-shift
sections. The audited retrieval results are recorded in
`evaluation/retrieval_comparison_artifact.json`.

Hosted validation ran the documented 77-test `unittest` suite successfully and
replayed the 15-question retrieval gold set with exact metric parity across
BM25, vector, hybrid, and reranked modes. The documented three-conversation
multi-turn smoke gate did not pass: route and outcome accuracy were 0.417,
required-evidence recall was 0.667, and clarification state remained unresolved
in `mt-009`. A judged repeat also exposed one scope-resolution error. The full
44-turn paid replay is intentionally deferred until those coordinator defects
are fixed; the hosted database and retrieval migration themselves remain
validated.

The legacy files remain in the ignored `data/` directory for the rollback
window. `DATABASE_URL` now targets hosted Supabase; source-PDF upload remains
deferred. Frontend Auth and cross-user verification remain deferred exactly as
described below.

## Decisions

- Use Supabase CLI timestamped SQL migrations. The project uses explicit SQL,
  so adding an ORM migration layer would create a second schema authority.
- Reference `auth.users(id)` directly. Do not create a duplicate application
  users table unless profile data is later required.
- Use `owner_id uuid` consistently. Every tenant-owned table carries it and a
  composite foreign key prevents a child row from claiming a different owner.
- Use application query filters and RLS. Foreign keys do not propagate RLS.
- Keep the FastAPI runtime on Psycopg 3 with an application connection pool.
  Prefer a direct connection for a persistent IPv6-capable deployment and the
  session pooler only when IPv4 requires it. Use a direct connection for
  migrations and backup/restore.
- Use weighted Postgres full-text search as the lexical baseline. `pg_trgm` is
  a possible measured fallback for misspellings and identifiers, not a
  replacement for full-text ranking.
- Store the current 3,072-dimensional embeddings in `vector(3072)` and begin
  with exact cosine search. Add a `halfvec(3072)` HNSW expression index only
  if measured latency warrants approximate search and its recall is validated
  against exact search.
- Use local Supabase CLI for reproducible schema and RLS integration tests.
  Do not use a shared hosted project for CI.

## Stages

Each stage is independently reviewable. SQLite and Chroma remain available as
rollback inputs until the Postgres retrieval evaluations pass; the shared
`./data` mount is removed only during final cleanup.

### Stage 0 - Freeze the baseline

- Back up `data/books.sqlite3`, `data/retrieval.sqlite3`, `data/chroma/`, and
  the source PDF.
- Record canonical row counts, the source file hash, chunk/build provenance,
  and a `restore_book()` equality check.
- Rebuild and freeze a current retrieval comparison. The older committed
  artifact used a 768-dimensional local model and is not the baseline for the
  active 3,072-dimensional OpenRouter index.
- Record the active BM25, vector, hybrid, and reranked Recall@5/MRR@5 metrics.

### Stage 1 - Database foundation

- Add `supabase/config.toml`, versioned migrations, and a local seed user.
- Enable `vector`; create canonical, derived, and embedding tables with
  Postgres-native types, foreign keys, constraints, and indexes.
- Enable RLS on every tenant-owned table. Policies compare `owner_id` with
  `auth.uid()`; direct server queries also filter explicitly by `owner_id`.
- Add the private `book-sources` Storage bucket and owner-scoped object
  policies. Database backup and Storage-object backup remain separate.
- Add Psycopg 3 and `psycopg_pool`; configure `DATABASE_URL`,
  `MIGRATION_DATABASE_URL`, and `DEFAULT_OWNER_ID`.
- Add a small database/repository boundary before changing callers.

### Stage 2 - Canonical storage and backfill

- Port canonical ingestion, restoration, scope reads, and content reads to
  Postgres. Preserve atomic replacement and lossless restoration.
- Scope every query by both `book_id` and server-derived `owner_id`.
- Add a deterministic SQLite-to-Postgres importer for the current canonical
  database. Derived rows are not copied; they are rebuilt.
- Upload original PDFs to the private Storage bucket when Supabase credentials
  are supplied and store the bucket/object key plus SHA-256 on `books`.
- Validate book/node/block/table/image counts, file hashes, hierarchy order,
  payload sizes, and restored `ParsedBook` equality before cutover.

### Stage 3 - Chunks and lexical retrieval

- Rebuild chunks in Postgres from canonical Postgres content in one atomic
  build replacement per owner/book/configuration. Use an advisory lock to
  prevent concurrent rebuilds for the same book.
- Generate a weighted `tsvector` from section title, hierarchy path, and body,
  and index the match predicate with GIN. Rank the Postgres-normalized lexeme
  positions with deterministic BM25 so the frozen FTS5 baseline is preserved.
- Preserve the current OR-term query behavior using safely parameterized
  `websearch_to_tsquery` input.
- Run `scripts.evaluate_retrieval` as the primary ranking comparison, then the
  multi-turn suite as a downstream regression. Report metric deltas rather
  than relying on a smoke test.

### Stage 4 - pgvector persistence and exact search

- Rebuild embeddings from the Postgres chunks; do not copy Chroma internals.
- Persist model name/revision, dimension, document-format version,
  embedding-input hash, and creation time with every vector.
- Do not keep a database transaction open during external embedding calls.
  Generate a batch, then upsert it in a short transaction.
- Run exact cosine search filtered by owner/book/model and compare vector,
  hybrid, and reranked results with the frozen baseline.
- Only add approximate HNSW later if latency is a measured problem. For the
  current 3,072 dimensions that index must use `halfvec`, not `vector`.

### Stage 5 - Cutover and cleanup

- Switch health checks from local-file existence to database queries.
- Update Docker/env/docs for `DATABASE_URL`; keep the data volume through the
  rollback window, then remove SQLite, Chroma, and their dependencies.
- Run the canonical audit, retrieval evaluation, multi-turn smoke set, and
  full local test suite before removing rollback data.
- Document the exact rollback procedure and retain the pre-cutover backup.

### Deferred follow-up - frontend Auth and multi-user verification

- Add Supabase login/logout/session refresh to Next.js and forward bearer
  tokens on normal and streaming requests.
- Verify JWT signature, issuer, audience, expiry, and subject in FastAPI and
  set transaction-local database claims/role when using RLS-backed direct SQL.
- Replace the numeric default book ID with an authenticated `/api/books`
  selector and add authenticated upload/delete flows.
- Add cross-user API, direct-SQL/RLS, Storage-policy, forged-state, and book-ID
  enumeration tests. Return 404 for inaccessible resources.
- Only after these tests pass may the deployment be called multi-user-safe.

## Rollback procedure

1. Stop writes and preserve a Postgres backup plus the independent Storage
   object backup.
2. Deploy the pre-cutover application revision.
3. Restore its ignored `data/books.sqlite3`, `data/retrieval.sqlite3`, and
   `data/chroma/` snapshot and mount `data/` as before.
4. Run the pre-cutover canonical round-trip and frozen retrieval checks before
   reopening traffic.
5. Keep the Postgres project intact until the rollback is verified; do not
   delete migrated data as part of the switchback.

## Cutover acceptance gates

1. Canonical counts and source SHA-256 match the SQLite snapshot.
2. `restore_book()` returns the same `ParsedBook` as the cached parser output.
3. Rebuilding chunks twice is idempotent and leaves one active build.
4. Every chunk source references a canonical block owned by the same user.
5. Embedding provenance matches the configured model and document format.
6. Retrieval metric deltas are reported; any accepted regression has a written
   failure analysis and justification.
7. The API health check succeeds against Postgres and no runtime code opens a
   local SQLite or Chroma store.
8. The pre-cutover data remains recoverable through the rollback window.
