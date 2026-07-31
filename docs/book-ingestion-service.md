# Multi-user book ingestion service

Status: proposed

Initial delivery: structured digital PDFs

Follow-up delivery: scanned and TOC-less PDFs with human review

## Summary

Expose a reliable asynchronous service that lets authenticated users upload
technical PDF books, processes them outside the request path, stores lossless
canonical content in Postgres, builds rebuildable retrieval data, and makes a
book queryable only after verification succeeds.

The first release supports digital PDFs with embedded text and an embedded
table of contents. Scanned and TOC-less PDFs are a separate workflow because
OCR alone cannot establish trustworthy chapter boundaries. That workflow adds
OCR, page-number alignment, and a user-confirmed table of contents before
canonical import.

The service is production-shaped for a modest multi-user workload:

- Supabase Auth, Postgres, pgvector, and private Storage.
- Next.js for upload and job progress.
- FastAPI for authenticated lifecycle endpoints.
- A separate Python ingestion worker.
- Postgres-backed durable jobs, leases, retries, and checkpoints.
- One worker job at a time initially.
- Railway as the initial application host.

Redis, Celery, RabbitMQ, Kafka, Temporal, Kubernetes, email notifications, and
automatic multi-worker scaling are explicitly deferred.

## Decisions

| Decision | Initial value |
|---|---|
| Ownership | Multi-user, one private library per authenticated user |
| Maximum source size | 50 MB / 52,428,800 bytes |
| Maximum pages | 1,000 |
| Source formats | PDF only |
| Initial document class | Digital PDF with embedded text and embedded TOC |
| Scanned PDFs | Separate follow-up workflow |
| Upload transport | Direct resumable TUS upload to private Supabase Storage |
| Job coordination | Postgres job table |
| Worker concurrency | One |
| User progress | Durable status polling |
| Canonical store | Supabase Postgres |
| Source object store | Private Supabase Storage |
| Retrieval readiness | Postgres FTS chunks and compatible pgvector embeddings |
| Application hosting | Railway web, API, and worker services |

The 50 MB limit must be enforced in the browser, Storage bucket configuration,
API metadata validation, and worker preflight. The page limit is independent:
a highly compressed PDF can be below 50 MB and still be expensive to parse.

## Goals

1. Let an authenticated user upload a supported PDF without holding a long
   FastAPI request open.
2. Preserve the original PDF and losslessly store the parsed hierarchy, text,
   tables, and images.
3. Make ingestion idempotent and safe to retry after process or infrastructure
   failure.
4. Prevent incomplete books from being selected for chat or retrieval.
5. Give users durable, understandable progress and actionable failures.
6. Isolate every job, database row, and Storage object by verified user
   identity.
7. Process large books with bounded memory using page batches.
8. Preserve parser, chunker, OCR, and embedding provenance.
9. Provide enough events, metrics, logs, and tests to explain and debug every
   ingestion.

## Non-goals for the first release

- Anonymous uploads.
- Shared libraries or collaborative book ownership.
- PDFs over 50 MB or 1,000 pages.
- Password-protected PDFs.
- Automatic hierarchy invention for scanned or TOC-less books.
- PowerPoint, EPUB, HTML, video, or audio ingestion.
- Multiple workers or priority queues.
- Cross-user source deduplication.
- Email, push, or webhook completion notifications.
- Approximate vector indexes.
- A LangGraph ingestion workflow.

Ingestion is deterministic orchestration and should remain plain Python.
LangGraph remains appropriate for the study workflow, where planning,
sufficiency checks, and bounded retries are inspectable agent decisions.

## User experience

### Structured PDF

1. The user signs in.
2. The user chooses a PDF.
3. The browser rejects an obviously invalid type or a file over the served limit.
4. FastAPI creates an owner-scoped ingestion job and immutable Storage path.
5. The browser uploads directly to private Storage with resumable TUS.
6. The browser tells FastAPI that the upload completed.
7. FastAPI verifies object ownership and metadata, then queues the job.
8. The UI shows the current stage and page/batch progress.
9. When verification succeeds, the book appears in the user's library.
10. If the job fails, the UI shows a safe error and whether retry is allowed.

Closing the browser does not affect the job. Reopening the application restores
status from Postgres.

### Scanned or TOC-less PDF

This flow is delivered separately:

1. Preflight classifies the document as scanned, mixed, or missing a TOC.
2. The worker performs bounded OCR and creates a derived searchable PDF.
3. The system proposes TOC pages and candidate hierarchy entries.
4. The job enters `needs_toc_review`.
5. The user confirms or edits the hierarchy and printed-page mapping.
6. The job resumes through parsing, canonical import, and retrieval builds.

The system must not silently treat model-proposed chapter boundaries as
canonical.

## Architecture

```text
Browser
  |
  |-- Supabase Auth
  |
  |-- POST /api/ingestions
  |       |
  |       `-- ingestion_jobs row + immutable Storage path
  |
  |-- TUS upload --------------------------------> private Supabase Storage
  |
  `-- POST /api/ingestions/{job_id}/complete
          |
          `-- status = queued
                  |
                  v
          Python ingestion worker
                  |
                  |-- validate and hash source
                  |-- parse/OCR bounded page batches
                  |-- atomically persist canonical content
                  |-- build deterministic chunks and FTS
                  |-- build provenance-checked embeddings
                  `-- verify and activate book
                          |
                          v
                 ready owner-scoped book
```

### Deployment

```text
Railway project
  web      Next.js
  api      slim FastAPI image
  worker   parser/OCR image, no public ingress

Supabase project
  Auth
  private book-sources bucket
  Postgres + pgvector
```

The API and worker should use separate Docker targets:

- The API image contains the HTTP, authentication, study, retrieval-query, and
  database code but not Torch, OCR, Poppler, or layout models.
- The worker image contains the full parser and OCR toolchain.

The worker filesystem is ephemeral. That is acceptable because the original
PDF and resumable checkpoints live in managed storage. Each job receives an
isolated temporary directory and cleans it on exit.

## Authentication and ownership

### Identity

Next.js uses Supabase Auth. FastAPI verifies every bearer access token against
the configured Supabase issuer and JWKS, including:

- signature and allowed algorithm;
- issuer;
- audience;
- expiration and not-before time;
- authenticated role;
- subject UUID.

The verified `sub` claim is the only source of `owner_id`. Request bodies,
query strings, frontend state, filenames, and book IDs never determine
ownership.

### Database authorization

Every application query is scoped by both the resource ID and verified owner:

```text
(owner_id, ingestion_job_id)
(owner_id, book_id)
(owner_id, storage_path)
```

Application filters and Postgres RLS are both required. Where the API connects
with a privileged database role, it sets transaction-local authenticated
claims/role or uses a narrowly reviewed repository method that always requires
an explicit verified owner.

The worker is trusted infrastructure and may use privileged credentials, but
it must carry the owner stored on the claimed job through every Storage and
database operation.

### Storage authorization

Source objects use immutable paths:

```text
{owner_id}/{job_id}/original.pdf
```

The original filename is metadata, not a path component. Upsert is disabled.
The private bucket permits an authenticated user to access only objects whose
owner and first path component match their user ID. Service credentials remain
server-side.

### Required isolation tests

User A must not be able to:

- list, read, update, cancel, or retry user B's job;
- list or query user B's book;
- upload to or overwrite user B's path;
- obtain a signed download URL for user B's source;
- use forged conversation state to select user B's book;
- learn whether user B uploaded a matching file hash.

Inaccessible resources return 404.

## Limits and quotas

Initial configurable limits:

| Limit | Value |
|---|---:|
| Source object size | 100 MiB |
| PDF pages | 1,000 |
| Active ingestion jobs per user | 1 |
| Queued ingestion jobs per user | 3 |
| Global parsing concurrency | 1 |
| Upload MIME types | `application/pdf` |

Additional limits must be configurable before public launch:

- books per user;
- total source bytes per user;
- extracted binary bytes per book;
- temporary bytes per job;
- OCR seconds per page;
- parser time per batch;
- embedding attempts and provider cost.

Limits are checked before expensive work whenever possible. Quota failures are
non-retryable and include a stable error code.

## API contract

Exact response models will be Pydantic contracts. The following defines the
required behavior, not final field naming.

### Create an ingestion

```http
POST /api/ingestions
Authorization: Bearer <token>
Idempotency-Key: <client-generated UUID>
Content-Type: application/json

{
  "original_filename": "technical-book.pdf",
  "content_type": "application/pdf",
  "content_length": 16238412
}
```

Response:

```http
201 Created

{
  "job_id": "uuid",
  "status": "awaiting_upload",
  "storage_bucket": "book-sources",
  "storage_path": "<owner-id>/<job-id>/original.pdf",
  "maximum_bytes": 104857600,
  "upload_method": "tus"
}
```

Creating the same request with the same owner and idempotency key returns the
same job. The endpoint rejects unsupported content type, declared oversize,
quota exhaustion, and excessive queued jobs.

### Complete an upload

```http
POST /api/ingestions/{job_id}/complete
Authorization: Bearer <token>
```

FastAPI verifies that the expected object exists, belongs to the caller, has an
allowed content type, and is within the byte limit. It then atomically changes
`awaiting_upload` to `queued`.

Response:

```http
202 Accepted
Location: /api/ingestions/{job_id}
```

The operation is idempotent. Repeating it for an already queued or running job
returns its current representation.

### Read job status

```http
GET /api/ingestions/{job_id}
Authorization: Bearer <token>
```

Example:

```json
{
  "job_id": "uuid",
  "status": "parsing",
  "stage": "parse_pages",
  "progress": {
    "completed": 275,
    "total": 640,
    "unit": "pages",
    "percent": 42.97
  },
  "attempt": 1,
  "retryable": false,
  "book_id": null,
  "error": null,
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```

Do not expose stack traces, SQL, provider response bodies, local paths, secrets,
or internal credentials.

### Cancel a job

```http
POST /api/ingestions/{job_id}/cancel
Authorization: Bearer <token>
```

Cancellation is cooperative. Queued jobs cancel immediately. Running jobs set
`cancellation_requested_at`; the worker stops at the next safe batch or stage
boundary.

### Retry a failed job

```http
POST /api/ingestions/{job_id}/retry
Authorization: Bearer <token>
```

Manual retry is allowed only for a failure marked retryable and only while the
original object still exists. The operation resumes from valid checkpoints.

### List books

```http
GET /api/books
Authorization: Bearer <token>
```

Only owner-scoped `ready` books are returned by default. The chat API requires
an explicit ready `book_id`; it must not default to book 1.

### Outline review

```http
GET  /api/ingestions/{job_id}/toc-proposal
POST /api/ingestions/{job_id}/toc-confirmation
```

Confirmation includes hierarchy levels, titles, source page ranges, and the
PDF destination page. Proposals for native-digital PDFs are deterministic,
non-canonical evidence. Confirmation is bound to the uploaded source hash,
strictly revalidated, and re-queues the same job. OCR-backed proposals and
printed-page alignment remain deferred to the scanned-document workflow.

## Job state machine

Initial structured states:

```text
awaiting_upload
  -> queued
  -> validating
  -> parsing
  -> persisting
  -> chunking
  -> embedding
  -> verifying
  -> ready
```

Cross-cutting/terminal states:

```text
retry_scheduled
failed
cancelled
```

Scanned follow-up states:

```text
classifying
ocr
needs_toc_review
```

State transitions are validated in one domain function. Arbitrary status
updates from API handlers or worker stages are prohibited.

`status` represents lifecycle. `stage` represents the current unit of work.
For example, `retry_scheduled` can retain `last_stage = embedding`.

## Database changes

Schema remains authoritative in timestamped Supabase SQL migrations.

### `ingestion_jobs`

Minimum columns:

- `id uuid primary key`
- `owner_id uuid not null`
- `idempotency_key uuid not null`
- `status text not null`
- `stage text`
- `document_class text`
- `storage_bucket text not null`
- `storage_path text not null`
- `original_filename text not null`
- `declared_content_type text`
- `declared_size_bytes bigint`
- `verified_size_bytes bigint`
- `file_hash text`
- `page_count integer`
- `book_id bigint`
- `progress_completed integer`
- `progress_total integer`
- `progress_unit text`
- `attempt_count integer not null`
- `max_attempts integer not null`
- `next_attempt_at timestamptz`
- `lease_owner text`
- `lease_expires_at timestamptz`
- `heartbeat_at timestamptz`
- `cancellation_requested_at timestamptz`
- `last_error_code text`
- `last_error_message text`
- `last_error_retryable boolean`
- parser, OCR, chunker, and embedding provenance fields or JSON objects
- `created_at`, `started_at`, `updated_at`, `completed_at`

Constraints:

- unique `(owner_id, idempotency_key)`;
- unique `(owner_id, storage_path)`;
- valid status/stage values;
- non-negative progress and attempt values;
- owner-scoped foreign key to `books` when `book_id` exists;
- partial unique index allowing one active processing job per owner.

### `ingestion_job_events`

Append-only important transitions:

- job ID and owner;
- monotonically increasing event ID;
- event type;
- stage/status;
- safe message and bounded metadata;
- timestamp.

Do not append one event per token, extracted block, or percentage point.

### `ingestion_batches`

Required before claiming 1,000-page support:

- job and owner;
- batch index;
- start and end PDF pages;
- status and attempt count;
- extraction artifact bucket/path;
- artifact hash and size;
- parser version/config hash;
- block/table/image counts;
- timestamps and safe error fields.

Unique `(job_id, batch_index)` makes batch execution idempotent.

### `books`

Add explicit lifecycle/readiness information:

- `status` such as `processing`, `ready`, `failed`;
- `ready_at`;
- originating ingestion job;
- active chunk build and embedding provenance where appropriate.

All study and retrieval entry points reject non-ready books.

### Binary image storage

The current base64 text representation should be evaluated before large
multi-user rollout. If retained in Postgres, prefer decoded `bytea` plus MIME
type and content hash to avoid base64 expansion while preserving lossless
canonical restoration. This change requires a measured migration and
round-trip test; it is not required merely to expose the first structured
upload.

## Worker queue

The worker polls Postgres and claims one eligible job using a short transaction
with `FOR UPDATE SKIP LOCKED`. Claim ordering is deterministic:

```text
next_attempt_at, created_at, id
```

The claim writes:

- worker identity;
- lease expiration;
- heartbeat timestamp;
- incremented attempt where appropriate;
- running status/stage.

The transaction commits before downloading, parsing, OCR, embedding calls, or
other long work.

### Lease and crash recovery

- The worker extends its lease periodically.
- A worker receiving SIGTERM stops claiming new work.
- Work stops at the next safe boundary when possible.
- A job with an expired lease becomes eligible for reclamation.
- Reclamation never assumes the previous stage did no work.
- Unique constraints, content hashes, stage completion records, and batch
  checkpoints make repeated execution safe.

The initial deployment runs one worker. The queue design still supports a
second worker later without changing job semantics.

## Structured ingestion pipeline

### 1. Upload verification

- Object exists at the exact job path.
- Object owner matches the job owner.
- Stored size is at most 104,857,600 bytes.
- MIME metadata is acceptable but is not trusted as proof of format.
- Filename is retained only as display metadata.

### 2. Local download and hashing

- Download into a job-specific temporary directory.
- Stream bytes through SHA-256.
- Enforce downloaded byte count.
- Reject a source that changes during or after validation.
- Use the original hash as immutable provenance.

Duplicate checks are owner-scoped. If the owner already has a ready book with
the same hash, the job completes as a duplicate and returns that existing book.
It must not delete or replace the existing book automatically.

### 3. Cheap PDF preflight

Use PyMuPDF before Unstructured:

- verify PDF signature and successful open;
- reject encryption/password requirement;
- read page count and enforce 1,000 pages;
- extract metadata and embedded TOC;
- Unicode-normalize TOC titles, collapse whitespace, and drop empty entries;
- preserve invalid levels and destinations as review evidence rather than
  inventing replacements;
- validate TOC presence, levels, titles, page ranges, ordering, and coverage;
- compare sampled headings with their destination-page text;
- count suspicious titles and same-page section collisions;
- sample embedded text coverage;
- sample image coverage, full-page rasters, and OCR text overlays;
- estimate total decoded image pixels as a parser-work diagnostic;
- classify structured, scanned, mixed, or TOC-less.

When a native-digital outline is missing or unsafe, a deterministic span
proposer uses numbering, font size, boldness, and repeated-margin filtering to
create a review-only hierarchy. It never silently replaces source metadata.
OCR-backed pages do not receive an automatic proposal because their text and
equation errors are precisely what require review.

Documents with an automatically approved normalized embedded outline continue
immediately. Native-digital documents with a deterministic review proposal
pause in `needs_toc_review`; after confirmation, the parser receives that exact
confirmed outline rather than re-reading the raw publisher rows. OCR-backed,
scanned, and mixed documents remain unsupported instead of running an unsafe
fallback.

### 4. Page-batched extraction

The current fixed global parser cache is not concurrency-safe and cannot be
used by the service. Each job uses:

```text
/tmp/study-partner-ingestion/{job_id}/
```

The source is partitioned into configurable page batches. A starting trial
range is 20–50 pages; the production default is chosen from benchmarks rather
than guesswork.

For every batch:

1. Create a temporary partial PDF without changing source page meaning.
2. Run the pinned Unstructured `hi_res` parser.
3. Preserve original page numbers.
4. Validate extracted elements.
5. Write the batch artifact atomically.
6. Hash and optionally checkpoint the artifact in private Storage.
7. Mark the batch complete.
8. Release page/model data before the next batch.

A corrupt or partial cache is never treated as complete merely because a file
exists.

### 5. Extraction quality gate

Record and validate:

- pages with non-empty extracted text;
- empty sections;
- total text characters;
- block, table, and image counts;
- invalid page references;
- unmatched placeholders/payloads;
- extracted binary size;
- suspiciously large individual elements;
- TOC/page coverage.

Structural contract failures are fatal. Quality anomalies may produce
`needs_review` in a later version, but the initial structured contract should
reject clearly unusable extraction.

When multiple sections start on one page, their ordered extracted heading
positions divide the page. If all boundaries cannot be resolved, parsing fails
instead of assigning the whole page to one section. Repeated top- and
bottom-margin text and page numbers remain in canonical storage with detected
header/footer categories, while derived retrieval and study contexts omit
them.

### 6. Canonical persistence

Adapt the existing atomic Postgres ingestion so it can consume ordered batch
artifacts without requiring all parser objects and image payloads in memory
simultaneously.

Canonical visibility remains atomic:

- all book/node/block/table/image rows commit; or
- none commit.

Store:

- original Storage bucket/path;
- original SHA-256;
- parser version/configuration;
- source metadata;
- page and hierarchy counts.

After commit, restore the canonical `ParsedBook` representation and compare it
against the validated parser contract or equivalent stable content hashes.

### 7. Deterministic chunk build

Run the existing citation-aware chunk builder:

- never cross a TOC node;
- preserve exact source block/page references;
- use the versioned configuration;
- retain the current advisory lock and atomic build replacement;
- verify that every chunk source belongs to the same owner and book.

No LLM is used.

### 8. Embeddings

Run the existing provenance-checked embedding synchronization:

- bounded batches;
- no transaction held during provider calls;
- retry only transient provider failures;
- respect rate-limit response guidance;
- reuse already compatible embeddings;
- validate dimension, model, revision, document format, and input hash.

If a worker dies after a provider response but before database commit, the
batch may be embedded again. This at-least-once cost is acceptable; data
correctness is preserved.

### 9. Readiness verification

Before marking the book ready:

- canonical restoration succeeds;
- node/block/table/image counts match extraction;
- at least one valid chunk exists;
- chunk source references are complete;
- compatible embedding count equals this book's active chunk count;
- configured BM25 and vector searches can address this ready book;
- parser/chunker/embedding provenance is recorded.

The `ready` transition is one short database transaction.

## Scanned and TOC-less follow-up

### Classification

Preflight distinguishes:

- digital text with embedded TOC;
- digital text without embedded TOC;
- scanned image pages;
- mixed digital and scanned pages;
- unsupported/corrupt input.

Classification rules and thresholds are versioned and evaluated on a small
reviewed fixture set.

### OCR

Use OCRmyPDF and Tesseract to produce a derived searchable PDF:

- preserve the original PDF unchanged;
- use skip mode for mixed documents;
- enable rotation correction;
- consider deskewing;
- do not enable destructive cleaning by default;
- begin with English and make language explicit;
- limit OCR concurrency and temporary disk;
- checkpoint bounded page ranges;
- record complete OCR provenance and output hash.

OCR output is derived and rebuildable from the original source and recorded
configuration.

### Structural roles

An outline entry's depth is not its role. Some books put chapters at level 1;
others group them under Parts and put chapters at level 2. Naming roles by
depth typed Part I as a chapter and Chapter 5 as a section, and only a node
typed `chapter` may answer to a chapter number, so every chapter of such a
book became unaddressable. Naming them by title instead fails the other way:
a book numbering its chapters "1 Introduction" rather than "Chapter 1" loses
all of them.

`parsing.outline_roles` uses both signals and classifies the book, not the
entry. Chapters are a consecutively numbered run of siblings at exactly one
depth, so the depth whose siblings yield the longest run of 1, 2, 3, ... is
the chapter level; roles then follow from position relative to it — `part`
above, `section`/`subsection`/`nested_section` below, `front_matter` and
`back_matter` beside. A run shorter than three entries is not a numbering
scheme, and the book falls back to the depth rule rather than inventing
structure.

Two properties keep this safe:

- **No role is excluded from scope search.** The earlier title rule was
  destructive because unmatched entries landed in a class search filtered out.
  An odd label costs a strange word in a listing; an invisible one costs whole
  books.
- **Ingestion refuses contradictory numbering.** Duplicate or gapped chapter
  numbers mean a reference would resolve to the wrong pages silently, which is
  worse than a failed ingestion.

Roles are assigned at ingestion, not during parsing, so correcting them on an
already-ingested book needs no re-parse, no OCR, and no rebuild of anything
derived:

```bash
uv run python -m scripts.reclassify_outlines --check
```

### Hierarchy review

For a missing embedded TOC:

1. Identify candidate TOC pages.
2. Extract candidate title, level, and printed page number.
3. Infer a proposed printed-page-to-PDF-page mapping.
4. Show the evidence to the user.
5. Require confirmation or correction.
6. Validate monotonic page boundaries and hierarchy levels.
7. Resume only after confirmation.

An LLM may produce a structured proposal, but every proposed entry must carry
page evidence and remain non-canonical until the user confirms it.

### OCR quality

Track:

- pages OCRed and skipped;
- empty OCR pages;
- text density distribution;
- OCR timeouts;
- TOC-entry alignment rate;
- chapter-heading matches;
- reviewed corrections;
- sampled citation correctness after ingestion.

The scanned workflow needs its own evaluation fixture and report before it is
described as supported.

## Retry policy

### Retryable

- Storage timeouts or transient 5xx responses.
- Database connection failures.
- Serialization failures and deadlocks.
- Provider rate limits, timeouts, and transient 5xx responses.
- Worker termination or expired lease.
- Temporary disk/infrastructure errors.
- A bounded batch infrastructure failure.

### Non-retryable

- Object over 100 MiB.
- PDF over 1,000 pages.
- Invalid, corrupt, or encrypted PDF.
- Missing TOC in structured-only mode.
- Unsupported language/document class.
- Invalid hierarchy or page ranges.
- Extracted content beyond configured safety limits.
- Deterministically invalid parser output.
- User cancellation.

### Backoff

Use capped exponential backoff with jitter. A starting policy is:

```text
30 seconds -> 2 minutes -> 10 minutes -> 30 minutes
```

Provider `Retry-After` takes precedence. Retry counts are stage-aware so a
provider outage does not repeatedly consume the parser attempt budget.

## Progress and notifications

The initial UI polls `GET /api/ingestions/{id}` every two to three seconds
while a job is active.

Useful progress units:

- uploaded bytes during TUS upload;
- validated source;
- parsed pages/batches;
- persisted blocks;
- built chunks;
- embedded chunks;
- verification checks.

The database is the source of truth. An SSE view may be added later by reading
durable state, but process-local queues are not used across the API and worker.

Email, push, and webhooks are deferred until usage shows that users commonly
leave before long OCR jobs finish.

## Health and observability

### Health endpoints

Separate:

- API liveness;
- API/database/schema readiness;
- worker heartbeat;
- queue health and oldest queued-job age;
- per-book retrieval completeness.

Starting a new book must not make the entire API return 503 merely because that
book has chunks but is still building embeddings. Global infrastructure
readiness and per-book completeness are different checks.

### Logs

Use structured JSON fields:

- correlation/request ID;
- job ID;
- owner ID or a safe stable hash;
- book ID;
- stage and batch;
- attempt;
- elapsed time;
- page/block/chunk counts;
- stable error code.

Never log:

- bearer tokens;
- API/service keys;
- database URLs;
- source PDF text;
- signed Storage URLs;
- full provider payloads containing book content.

### Metrics

Minimum operational metrics:

- jobs created, ready, failed, cancelled, and retried;
- queued and running jobs;
- oldest queued-job age;
- worker heartbeat age;
- duration by stage;
- pages per second;
- peak worker memory and temporary disk from deployment metrics;
- parser/OCR failures by code;
- embedding batches, retries, latency, and approximate cost;
- ready-book chunk/embedding mismatch count.

LangSmith continues to trace the LangGraph study flow and LLM calls. It is not
used as the ingestion job database.

## Cleanup and retention

- Successful original PDFs are retained for recovery and rebuild.
- Canonical Postgres content is retained until the owner deletes the book.
- Derived parser checkpoints are deleted after a successful verification plus
  a short debugging window.
- Failed-job checkpoints and sources use a configurable retention policy and
  are disclosed to the user.
- Cancelled pre-import jobs delete temporary artifacts and, after the retention
  window, the uploaded source.
- Deleting a ready book removes canonical and derived rows through owner-safe
  cascades and separately removes Storage objects.

Postgres backups and Storage-object recovery are separate operational concerns
and both need a documented restore test.

## Security hardening

- Run worker containers as non-root.
- Give the worker no inbound public service.
- Use a read-only root filesystem where the platform permits it.
- Write only to a bounded job-specific temporary directory.
- Never execute user-supplied commands, paths, SQL, or model-generated SQL.
- Validate actual file structure, not extension/MIME alone.
- Pin parser/OCR/system dependencies through the existing lock/image.
- Bake or warm parser model assets rather than downloading them during the
  first user job.
- Apply CPU, memory, runtime, output-size, and temporary-disk guards.
- Do not serve uploaded PDFs publicly.
- Review malware scanning or stronger sandboxing before accepting uploads from
  untrusted public users at scale.

## Testing strategy

### Unit tests

- state-transition rules;
- retry classification and backoff;
- owner derivation;
- quota checks;
- progress calculation;
- batch boundaries;
- safe error serialization;
- document classification;
- cancellation boundaries.

### Postgres integration tests

- job claiming with `SKIP LOCKED`;
- lease expiry and reclamation;
- one active job per owner;
- idempotent create/complete/retry;
- duplicate owner/file hash;
- canonical transaction rollback;
- batch checkpoint uniqueness;
- ready-book filtering;
- RLS and cross-user isolation.

### Storage tests

- owner-scoped upload/read/delete;
- immutable path and no upsert;
- 100 MiB bucket enforcement;
- forged owner path denial;
- worker access without leaking service credentials.

### Pipeline fixtures

- current representative structured book;
- small valid structured fixture;
- corrupt PDF;
- encrypted PDF;
- missing TOC;
- invalid TOC hierarchy;
- empty/low-text PDF;
- table/image fixture;
- mixed digital/scanned fixture;
- large synthetic page-count fixture;
- reviewed scanned fixture for the follow-up.

### Failure injection

Terminate or raise a controlled failure:

- after upload verification;
- after hashing;
- after selected parse batches;
- before and after canonical commit;
- during chunk build;
- between embedding provider response and database commit;
- before ready transition.

Every case must converge to one correct book or one clear terminal job without
duplicate canonical/derived data.

### Performance validation

Benchmark:

- the existing full book;
- a representative near-1,000-page digital book;
- a substantial scanned book before enabling OCR publicly.

Record peak RSS, temporary disk, total time, per-stage time, pages per second,
provider latency, tokens/cost, and retry behavior. Set worker resources and
batch size with at least 25–30% measured headroom.

## CI and deployment gates

Required before deployment:

1. Python unit and Postgres integration tests pass.
2. Frontend build and production dependency audit pass.
3. API and worker images build independently.
4. Worker image completes a real small PDF parse smoke test.
5. Storage policies pass cross-user tests.
6. Migrations apply from an empty local Supabase project.
7. Existing retrieval metrics do not regress for the representative book.
8. A crash-recovery ingestion test reaches one ready book.
9. No request defaults to a global/bootstrap owner or book ID.

Railway configuration should provide:

- web/API health checks;
- worker restart policy;
- independent API and worker resource limits;
- deployment secrets;
- log and resource metrics;
- spending alerts and a hard budget limit.

Do not enable idle/serverless sleep for a worker that polls Postgres.

## Delivery plan

### Phase 1: multi-user foundation

- Supabase Auth in Next.js.
- FastAPI JWT verification.
- Request-derived owner scope.
- Authenticated `/api/books`.
- Removal of `DEFAULT_OWNER_ID` and `book_id=1` from public runtime behavior.
- Cross-user API, SQL/RLS, Storage, and forged-state tests.

### Phase 2: structured ingestion MVP

- Promote the private `book-sources` bucket migration with a 100 MiB limit.
- Add ingestion job/event schema.
- Add create, complete, status, cancel, and retry endpoints.
- Add direct resumable upload UI.
- Add one lease-based worker.
- Refactor manual parse/import/chunk/embed commands into reusable stages.
- Expose progress and ready books.

### Phase 3: large-book reliability

- Add page batching and batch checkpoints.
- Make parser caches job-specific and atomic.
- Add resource/output guards.
- Add 1,000-page fixtures and benchmarks.
- Add crash recovery at each boundary.
- Finalize worker CPU/RAM from measurements.

The service must not claim 1,000-page support before this phase passes.

### Phase 4: scanned and TOC-less workflow

- Add OCRmyPDF and provenance.
- Add document classification.
- Add TOC proposal and evidence.
- Add page-number alignment and confirmation UI.
- Add resume from `needs_toc_review`.
- Add scanned-document evaluation and quality gates.

### Phase 5: operational hardening

- Separate production images and Railway services.
- Add worker/queue monitoring.
- Add cleanup and retention jobs.
- Run backup/restore exercise.
- Conduct dependency and untrusted-PDF security review.
- Document recovery, incident, and manual job-repair procedures.

## Definition of done: structured ingestion

The first feature is done when:

1. Two authenticated users can upload books and cannot access each other's
   jobs, objects, books, or chat scopes.
2. A valid supported PDF up to 100 MiB and 1,000 pages uploads resumably.
3. Processing continues after the browser disconnects.
4. A worker crash safely resumes without duplicate canonical data.
5. The original source remains private and hash-verifiable.
6. Canonical restoration is lossless.
7. Chunks retain exact citation sources.
8. Compatible embeddings exist for every active chunk.
9. Only verified ready books are selectable.
10. Invalid and unsupported PDFs fail with actionable, non-sensitive errors.
11. Health checks remain healthy while another book is processing.
12. Tests, deployment configuration, operational metrics, and recovery
    documentation are committed.

## Definition of done: scanned ingestion

Scanned support is done only when:

1. The original PDF remains unchanged and OCR output is rebuildable.
2. OCR provenance and page outcomes are recorded.
3. Missing hierarchy requires reviewed confirmation.
4. Printed and PDF page numbers are explicitly aligned.
5. OCR/TOC failures abstain or request correction rather than inventing
   boundaries.
6. A reviewed scanned-book fixture produces acceptable extraction, retrieval,
   and citation results.
7. The resource and cost profile is measured on a substantial scanned book.

## Deferred upgrade triggers

Add Redis and a packaged task queue only if measurements show one or more of:

- sustained queue depth that a single worker cannot drain;
- multiple worker pools with different hardware or priorities;
- more than two concurrent workers;
- materially harmful Postgres polling/locking overhead;
- many unrelated background task types;
- scheduling requirements that exceed the simple retry model.

Consider on-demand Cloud Run Jobs only if the Railway worker is mostly idle and
its measured idle cost justifies the added dispatch, IAM, and reconciliation
complexity.

## Open implementation decisions

These do not block the feature definition but must be resolved with tests or
measurements:

- final parser page-batch size;
- worker CPU/RAM and stage timeouts;
- whether to migrate canonical images from base64 text to `bytea`;
- failed source/checkpoint retention duration;
- per-user book and storage quotas;
- whether a structured extraction anomaly should fail or enter manual review;
- the minimum acceptable OCR and TOC-alignment metrics;
- exact Railway plan for public beta versus portfolio demonstration.
