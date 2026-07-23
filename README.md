# Agentic Study Partner

An evaluation-driven study companion for technical books. It parses PDFs into
a lossless hierarchical model, stores canonical and rebuildable retrieval data
in Supabase Postgres, and uses an inspectable LangGraph workflow to produce
grounded answers and complete-scope summaries with citations.

The backend currently runs in **bootstrap single-user mode**. It scopes every
query by `DEFAULT_OWNER_ID`, but the Next.js Auth flow and cross-user isolation
tests are intentionally deferred. See
[`docs/supabase-migration-plan.md`](docs/supabase-migration-plan.md) for the
cutover plan and the exact deferred work.

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

- `storage/postgres.py`: canonical validation, ingestion, and restoration.
- `storage/database.py`: pooled Postgres connections and bootstrap owner.
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
DEFAULT_OWNER_ID=00000000-0000-4000-8000-000000000001
```

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
to FastAPI at `http://localhost:8000`. The interface remains intentionally
single-user until the deferred Supabase Auth stage is implemented.

The API exposes `GET /api/health` and `POST /api/chat`. Health returns 503 when
the canonical schema, pgvector schema, or configured embedding provenance is
not ready. Every study turn and LLM call can be traced in LangSmith when the
standard LangSmith environment variables are configured.

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

Start Supabase on the host first, then run the API and frontend containers:

```bash
npx --yes supabase@2.109.1 start
docker compose up --build
```

For local Supabase, the API container reaches the host database through
`DOCKER_DATABASE_URL`. When that override is absent, Compose uses
`DATABASE_URL`, so the same image can connect directly to hosted Supabase;
unset `DOCKER_DATABASE_URL` in that case. The API container has no local
database volume; canonical and derived retrieval data live in Postgres.
