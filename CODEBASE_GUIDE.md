# Codebase guide

The active pipeline is:

`PDF → ParsedBook → Supabase Postgres → FTS/pgvector → LangGraph → FastAPI → UI/CLI → evaluations`

## Runtime

| Path | Responsibility |
|---|---|
| `parsing/` | Extract a PDF into the lossless hierarchical `ParsedBook` contract. |
| `storage/database.py` | Pool Postgres connections and resolve the temporary server-controlled owner. |
| `storage/postgres.py` | Validate, ingest, and restore canonical book content transactionally. |
| `storage/conversations.py` | Owner-scoped conversation and turn persistence. |
| `retrieval/chunking.py` | Build deterministic citation-aware chunks from canonical blocks. |
| `retrieval/postgres.py` | Atomically persist chunks and run weighted Postgres full-text/BM25 retrieval. |
| `retrieval/vector.py` | Synchronize provenance-checked `vector(3072)` rows and run exact cosine search. |
| `retrieval/search.py` | Expose lexical, vector, RRF hybrid, and hosted-reranker strategies. |
| `study/` | Resolve hierarchy scopes, load complete evidence, validate citations, and orchestrate turns with LangGraph. |
| `ingestion/` | Limits, job state machine, retry policy, durable job queue, PDF preflight, and the ingestion pipeline. |
| `decks/` | Flashcards over one chapter or lecture: deterministic topic inventory, per-topic generation with coverage repair, citation validation, SM-2 scheduling, and the generation queue. |
| `interviews/` | Adaptive chapter/lecture interview planning, grounded LangGraph answer evaluation, resumable checkpoints, scoring, screen observations, and OpenRouter speech. |
| `worker/main.py` | Poll Postgres, claim one job under a lease, run the pipeline, and record the outcome. Rotates over the book, video, and deck queues. |
| `api/auth.py` | Verify Supabase access tokens and derive `owner_id` from the token subject. |
| `api/ingestions.py` | Owner-scoped upload lifecycle: create, complete, status, list, cancel, retry. |
| `api/main.py` | Serve health, the ready-book library, chapter outlines, conversation CRUD, synchronous chat, and SSE streaming. |
| `api/decks.py` | Owner-scoped decks: queue a generation, list decks, read one, serve the daily review queue, grade a card. |
| `api/interviews.py` | Owner-scoped interview lifecycle plus ephemeral STT, TTS, and explicit screen-checkpoint endpoints. |
| `frontend/` | Next.js + TypeScript interface built on Tailwind v4 and shadcn/ui: Supabase sign-in, resumable upload with durable job progress, the ready-book library, and grounded streaming chat. |

## Database authority

| Path | Responsibility |
|---|---|
| `supabase/migrations/` | Authoritative active schema, constraints, indexes, and RLS. |
| `supabase/seed.sql` | Stable local bootstrap owner without login credentials. |
| `supabase/config.toml` | Reproducible local Supabase configuration. |
| `supabase/deferred/` | Reviewed but inactive Storage SQL for the future upload API. |

Canonical tables are `books`, `nodes`, `content_blocks`, `table_blocks`, and
`image_blocks`. Derived tables are `chunk_builds`, `chunks`, `chunk_sources`,
and `chunk_embeddings`; their contents are always rebuildable. `decks`,
`deck_topics`, and `deck_cards` are derived the same way — regenerating a scope
inserts a new version rather than mutating cards, so review history survives.
`deck_review_events` is the exception and is canonical: it is the append-only
record of what was reviewed, and `deck_card_reviews` is a checkpoint over it.
`interview_sessions` stores the resumable clock, source snapshot, aggregate
scores, and derived workflow checkpoint. `interview_turns` is the canonical
question/answer/evaluation record. Raw microphone clips and screen images are
deliberately absent from both tables.
`conversations`
and `conversation_turns` hold study history: turns are canonical and
`conversations.state_json` is a derived resume checkpoint, stored rather than
replayed because rebuilding it every turn is wasted work. `ingestion_jobs`
and `ingestion_job_events` carry the durable upload lifecycle: the job table is
the queue, the lease, the checkpoint, and the progress source of truth.

The pre-cutover SQLite and Chroma implementations have been retired. Runtime
and tests use the same Postgres canonical-storage and retrieval boundaries.

## Operational commands

| Script | Responsibility |
|---|---|
| `scripts/parse_book.py` | Parse the configured PDF and cache `ParsedBook` JSON. |
| `scripts/import_book.py` | Import cached parser output into canonical Postgres. |
| `scripts/build_chunks.py` | Rebuild Postgres chunks and lexical search data. |
| `scripts/build_vector_index.py` | Synchronize OpenRouter embeddings into pgvector. |
| `scripts/inspect_scope.py` | Inspect hierarchy and canonical content without an LLM call. |
| `scripts/study.py`, `scripts/ask_book.py` | Run explicit-scope and general grounded queries. |
| `scripts/evaluate_retrieval.py` | Compare all frozen retrieval modes. |
| `scripts/evaluate_multiturn.py` | Replay the stateful conversation gold set. |
| `scripts/build_*_report.py`, `scripts/render_retrieval_report.py` | Produce inspectable evaluation artifacts and self-contained review pages. |
| `python -m worker.main` | Run the ingestion worker. `--once` processes a single job and exits. |

The manual scripts remain the local path for a book you already have on disk.
The worker is the multi-user path: it does the same work from an uploaded
source, with leases, retries, verification, and durable progress.

## Evaluation and tests

`evaluation/` contains frozen retrieval/multi-turn judgments and generated
reports. `evals/` contains reusable scoring/replay code. Tests cover canonical
round trips, Postgres chunk/vector rebuilds, ranking, scope resolution,
summaries, citations, conversations, the API, report logic, token
verification, job-queue behaviour, PDF classification, and the ingestion
pipeline end to end.

Most application tests provision a temporary owner in local Supabase and
cascade-delete it afterward. `tests/test_multi_user_isolation.py` provisions
two owners and asserts isolation through both application filters and RLS.
`tests/pdf_fixtures.py` generates PDFs with PyMuPDF, so no binary fixtures are
committed. The Storage-backed ingestion tests skip unless `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY` are configured.

## Local and generated data

| Path | Status |
|---|---|
| `sources/books/` | Original local PDF inputs; ignored and not distributable by default. |
| `cache/` | Rebuildable parser caches; safe to clear after a successful import. |
| `data/` | Reserved for local data; retired SQLite and Chroma stores are not retained. |
| `outputs/`, `evaluation/runs/` | Generated summaries and routine evaluation outputs. |
| `.env`, `.venv/`, `__pycache__/` | Local secrets/environment/cache; never source artifacts. |

Architecture decisions are documented in
[`docs/architecture.md`](docs/architecture.md), the database cutover history in
[`docs/supabase-migration-plan.md`](docs/supabase-migration-plan.md), and the
upload service design in
[`docs/book-ingestion-service.md`](docs/book-ingestion-service.md).
