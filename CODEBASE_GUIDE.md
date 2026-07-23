# Codebase guide

The active pipeline is:

`PDF → ParsedBook → Supabase Postgres → FTS/pgvector → LangGraph → FastAPI → UI/CLI → evaluations`

## Runtime

| Path | Responsibility |
|---|---|
| `parsing/` | Extract a PDF into the lossless hierarchical `ParsedBook` contract. |
| `storage/database.py` | Pool Postgres connections and resolve the temporary server-controlled owner. |
| `storage/postgres.py` | Validate, ingest, and restore canonical book content transactionally. |
| `retrieval/chunking.py` | Build deterministic citation-aware chunks from canonical blocks. |
| `retrieval/postgres.py` | Atomically persist chunks and run weighted Postgres full-text/BM25 retrieval. |
| `retrieval/vector.py` | Synchronize provenance-checked `vector(3072)` rows and run exact cosine search. |
| `retrieval/search.py` | Expose lexical, vector, RRF hybrid, and hosted-reranker strategies. |
| `study/` | Resolve hierarchy scopes, load complete evidence, validate citations, and orchestrate turns with LangGraph. |
| `api/main.py` | Serve health, synchronous chat, and SSE streaming over the shared study workflow. |
| `frontend/` | Provide the minimal Next.js chat interface. Supabase Auth is intentionally deferred. |

## Database authority

| Path | Responsibility |
|---|---|
| `supabase/migrations/` | Authoritative active schema, constraints, indexes, and RLS. |
| `supabase/seed.sql` | Stable local bootstrap owner without login credentials. |
| `supabase/config.toml` | Reproducible local Supabase configuration. |
| `supabase/deferred/` | Reviewed but inactive Storage SQL for the future upload API. |

Canonical tables are `books`, `nodes`, `content_blocks`, `table_blocks`, and
`image_blocks`. Derived tables are `chunk_builds`, `chunks`, `chunk_sources`,
and `chunk_embeddings`; their contents are always rebuildable.

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

## Evaluation and tests

`evaluation/` contains frozen retrieval/multi-turn judgments and generated
reports. `evals/` contains reusable scoring/replay code. Tests cover canonical
round trips, Postgres chunk/vector rebuilds, ranking, scope resolution,
summaries, citations, conversations, the API, and report logic.

Most application tests provision a temporary owner in local Supabase and
cascade-delete it afterward. These fixtures test single-owner behavior only;
cross-user/RLS security testing belongs to the deferred Auth stage.

## Local and generated data

| Path | Status |
|---|---|
| `sources/books/` | Original local PDF inputs; ignored and not distributable by default. |
| `cache/` | Rebuildable parser caches; safe to clear after a successful import. |
| `data/` | Reserved for local data; retired SQLite and Chroma stores are not retained. |
| `outputs/`, `evaluation/runs/` | Generated summaries and routine evaluation outputs. |
| `.env`, `.venv/`, `__pycache__/` | Local secrets/environment/cache; never source artifacts. |

Architecture decisions and the temporary single-user boundary are documented
in [`docs/architecture.md`](docs/architecture.md) and
[`docs/supabase-migration-plan.md`](docs/supabase-migration-plan.md).
