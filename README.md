# Agentic Study Partner

An evaluation-driven study companion for technical books. It parses PDFs into
a lossless hierarchical model, stores canonical and rebuildable retrieval data
in Supabase Postgres, and uses an inspectable LangGraph workflow to produce
grounded answers and complete-scope summaries with citations.

The backend is **multi-user**. Every request derives its owner from a verified
Supabase access token, and an asynchronous worker ingests uploaded PDFs without
holding a request open. The browser upload and sign-in UI is still in progress;
until it lands, the API is exercised through tokens directly. See
[`docs/book-ingestion-service.md`](docs/book-ingestion-service.md) for the
service design and [`docs/supabase-migration-plan.md`](docs/supabase-migration-plan.md)
for the database cutover history.

## Architecture

```text
PDF
 └─ parsing.parser.parse_book()
     └─ ParsedBook
         └─ Supabase Postgres (canonical)
             ├─ books / nodes / content_blocks
             ├─ table_blocks / image_blocks
             └─ rebuildable retrieval data
                 ├─ chunk_builds / chunks / chunk_sources
                 ├─ generated weighted tsvector (lexical search)
                 └─ chunk_embeddings vector(3072) (exact semantic search)

Next.js → FastAPI → LangGraph → complete-scope load or retrieval → cited answer
```

The source/derived boundary is unchanged:

- `books`, `nodes`, `content_blocks`, `table_blocks`, and `image_blocks` are
  canonical and lossless.
- `chunk_builds`, `chunks`, `chunk_sources`, full-text vectors, and embeddings
  are derived and always rebuildable.
- Complete chapter/section summaries load the full canonical subtree rather
  than relying on top-k retrieval.
- Ordinary questions search chunks and must abstain when evidence is
  insufficient.

Important modules:

- `storage/postgres.py`: canonical validation, ingestion, restoration, and
  book readiness.
- `storage/database.py`: pooled Postgres connections and owner validation.
- `api/auth.py`: Supabase token verification and request-derived ownership.
- `ingestion/`: limits, job state machine, retry policy, durable queue, PDF
  preflight, and the ingestion pipeline.
- `worker/main.py`: the lease-based ingestion worker.
- `retrieval/postgres.py`: deterministic chunk builds and full-text search.
- `retrieval/vector.py`: provenance-checked pgvector synchronization and exact
  cosine search.
- `retrieval/search.py`: BM25, vector, RRF hybrid, and reranked strategies.
- `study/graph.py`: inspectable LangGraph study-turn workflow.
- `api/main.py`: FastAPI health and chat boundary.
- `supabase/migrations/`: schema, constraints, RLS, and Storage policies.

More detail is in [`docs/architecture.md`](docs/architecture.md), and a concise
repository map is in [`CODEBASE_GUIDE.md`](CODEBASE_GUIDE.md).

## Setup

Install Python dependencies and create local configuration:

```bash
uv sync --frozen
test -f .env || cp .env.example .env
```

PDF parsing dependencies remain part of the deployed FastAPI image so the
existing parser is available to the planned upload/ingestion API. Embedding
and reranking inference remain hosted and do not add local model dependencies.

Start local Supabase and apply the checked-in migration and seed:

```bash
npx supabase start
npx supabase db reset
```

The defaults in `.env.example` match the Supabase CLI database:

```text
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
SUPABASE_URL=http://127.0.0.1:54321
DEFAULT_OWNER_ID=00000000-0000-4000-8000-000000000001
```

`SUPABASE_URL` is the issuer the API verifies access tokens against and the
Storage endpoint the API and worker use with `SUPABASE_SERVICE_ROLE_KEY`.
`DEFAULT_OWNER_ID` is now used only by local CLI and evaluation commands;
request handlers derive the owner from the verified token subject and never
read it.

For hosted Supabase, use the pooled application connection as `DATABASE_URL`
and the direct connection as `MIGRATION_DATABASE_URL`. Do not expose either
connection string or the service-role key to the browser.

## Import a book

```bash
uv run python -m scripts.parse_book
uv run python -m scripts.import_book
```

The parser cache is rebuildable from the source PDF. Canonical content lives
in Postgres after import; the retired SQLite migration path and local vector
store are no longer part of the repository or runtime.

## Upload a book through the ingestion service

This is the multi-user path. An authenticated user reserves a job, uploads
directly to private Storage, and the worker does the rest outside the request:

```text
POST /api/ingestions            reserve {owner_id}/{job_id}/original.pdf
  -> upload to private Storage  resumable, 50 MiB and application/pdf only
POST /api/ingestions/{id}/complete   verify the stored object, queue the job
GET  /api/ingestions/{id}       durable status while the worker runs
GET  /api/books                 the book appears only after verification
```

`POST /api/ingestions` requires an `Idempotency-Key` header, so a retried
request returns the original job instead of reserving a second upload path.
Cancel and retry live at `/api/ingestions/{id}/cancel` and `/retry`; retry is
allowed only for a failure the worker marked retryable.

Run the worker alongside the API:

```bash
uv run python -m worker.main
```

It claims one job at a time with `FOR UPDATE SKIP LOCKED`, holds a lease it
renews while working, and stops at a safe boundary on `SIGTERM`. A crashed
attempt is reclaimed once its lease expires and resumes from the canonical
import when one committed. Use `--once` to process a single job and exit.

The first release accepts digital PDFs with embedded text and an embedded
table of contents. Scanned, mixed, and outline-less PDFs are classified and
refused with a specific reason rather than guessed at, because OCR alone
cannot establish trustworthy chapter boundaries.

Current limits, all configurable:

| Limit | Value |
|---|---:|
| Source object size | 50 MiB |
| PDF pages | 400 |
| Pending jobs per user | 3 |
| Worker concurrency | 1 |

The page cap stays below the 1,000-page design target until page-batched
parsing and its benchmarks land.

## Build retrieval data

Create citation-aware chunks and the generated Postgres full-text index:

```bash
uv run python -m scripts.build_chunks --book-id 1
```

The default policy targets 600 tokens, caps chunks at 800, uses up to 80
tokens of block-aligned overlap, and never crosses a TOC node. Rebuilding the
same book/configuration is atomic and deterministic.

Build 3,072-dimensional OpenRouter embeddings in Postgres:

```bash
uv run python -m scripts.build_vector_index --book-id 1
```

This requires `OPENROUTER_API_KEY`; the model defaults to
`OPENROUTER_EMBEDDING_MODEL`. The current corpus is intentionally queried with
exact pgvector cosine search. Approximate indexes are deferred until evaluation
or corpus growth shows a latency need; pgvector HNSW `vector` indexes do not
support the current 3,072 dimensions.

## Inspect and query

Inspect a complete canonical chapter hierarchy:

```bash
uv run python -m scripts.inspect_scope chapter 1 --book-id 1
```

Dry-run a full chapter summary before paying for a model call:

```bash
uv run python -m scripts.study "Summarize Chapter 1" --book-id 1 --dry-run
```

Ask a grounded question through the same coordinator used by the web app:

```bash
uv run python -m scripts.ask_book \
  --retrieval-mode hybrid \
  "How does reservoir sampling work?"
```

## Evaluation

Run only the frozen lexical baseline:

```bash
uv run python -m scripts.evaluate_retrieval --modes bm25
```

After embeddings exist, compare all retrieval strategies:

```bash
uv run python -m scripts.evaluate_retrieval
uv run python -m scripts.build_retrieval_report
```

The gold set and judgment policy live under `evaluation/`. Reports include
overall/category metrics, each expected node, retrieved citations, excerpts,
and explicit missing-evidence diagnostics. The audited Postgres comparison is:

| Method | Recall@3 | Recall@5 | MRR@5 |
|---|---:|---:|---:|
| BM25 | 0.819 | 0.889 | 0.917 |
| Vector | 0.903 | 0.931 | 0.792 |
| Hybrid | 0.847 | 0.931 | 0.847 |
| Hybrid + reranker | 0.889 | 1.000 | 0.958 |

The reranker candidate Recall@20 is 1.000. Hybrid remains the interactive
default until traced latency and cost justify paying for reranking on every
request.

Validate the synthetic multi-turn fixture and build its offline inspection
page with:

```bash
uv run python -m scripts.validate_multiturn_gold
uv run python -m scripts.build_multiturn_report
```

## API and UI

After configuring model credentials, start FastAPI:

```bash
uv run uvicorn api.main:app --reload
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:3000`. The browser calls Next.js `/api`, which forwards
to FastAPI at `http://localhost:8000`. The sign-in and upload interface is
still in progress, so the browser client cannot yet authenticate.

The API exposes `GET /api/health`, `GET /api/books`, `POST /api/chat`,
`POST /api/chat/stream`, and the `/api/ingestions` lifecycle. Everything except
health requires a Supabase bearer token.

Health reports infrastructure readiness only: it returns 503 when the canonical
or retrieval schema is missing, and stays healthy while a book is being
ingested. Per-book completeness is reported per book by `GET /api/books`.

Every study turn and LLM call can be traced in LangSmith when the standard
LangSmith environment variables are configured. LangSmith is not used as the
ingestion job database; Postgres is.

## Verification

Run Python checks:

```bash
uv run python -m compileall -q api evals parsing retrieval scripts storage study tests
uv run python -m unittest discover -s tests -v
```

Audit and build the Next.js client:

```bash
cd frontend
npm ci
npm audit --omit=dev
npm run build
```

GitHub Actions runs the Python and frontend checks on pushes to `main` and pull
requests. Postgres integration tests require the local Supabase stack.

## Docker

Start Supabase on the host first, then run the API, worker, and frontend
containers:

```bash
npx --yes supabase@2.109.1 start
docker compose up --build
```

For local Supabase, the containers reach the host database and Storage through
`DOCKER_DATABASE_URL` and `DOCKER_SUPABASE_URL`. When those overrides are
absent, Compose uses `DATABASE_URL` and `SUPABASE_URL`, so the same image can
connect directly to hosted Supabase; unset the overrides in that case.

The API and worker share one image and differ only by command: the worker runs
`python -m worker.main` and publishes no port. Splitting them into a slim API
image without the parser toolchain is deferred to deployment hardening. Neither
container keeps a local database volume, and the worker's filesystem is
disposable: the source PDF and job state both live in managed storage, so
losing the container costs one attempt rather than a book.
