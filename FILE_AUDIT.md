# Repository File Audit

Audit date: 2026-07-23
Branch: `deployment_readiness`

This is a deployment-focused inventory of the repository. It answers three
questions for every source-controlled project file: what it does, whether it
is still needed, and why. Ignored build output and repeated generated results
are grouped at the end.

## Status key

| Status | Meaning |
|---|---|
| **Yes** | Keep. It supports the active product, deployment, tests, or required documentation. |
| **Temporary** | Keep only for a named migration, rollback, or transition. |
| **No** | Remove, keep deleted, or regenerate. It is stale or has no active caller. |
| **Local only** | Useful on a developer machine, but never commit it. |

## Folder summary

| Folder | Needed? | Role and decision |
|---|---|---|
| `api/` | Yes | Production FastAPI boundary. |
| `parsing/` | Yes | Required PDF parsing contracts and implementation for the future upload API. |
| `retrieval/` | Yes | Active Postgres FTS, pgvector, hybrid, and hosted-reranking pipeline. Its old SQLite pair is retired. |
| `storage/` | Yes | Canonical Postgres persistence. The SQLite migration boundary is retired. |
| `study/` | Yes | LangGraph conversation, hierarchy, grounding, citation, and summary workflow. |
| `scripts/` | Yes | Manual ingestion, rebuild, diagnostic, and evaluation entry points. One migration script is temporary. |
| `evals/` | Yes | Evaluation logic and inspectable report generation. |
| `evaluation/` | Yes | Gold sets and selected portfolio evidence. Two rendered HTML files are stale. |
| `frontend/` | Yes | Minimal production-shaped Next.js interface. |
| `supabase/` | Yes | Authoritative Postgres migrations and local Supabase configuration. Upload storage SQL is planned but not active. |
| `tests/` | Yes | Deployment and behavior regression coverage, including canonical Postgres lifecycle tests. |
| `docs/` | Yes | Architecture and decision records; some migration wording needs correction. |
| `.github/` | Yes | CI for backend, frontend, database, parser, and image boundaries. |
| Local/generated folders | No as source | `.venv`, caches, builds, local books, old databases, and routine run output must stay ignored. |

## Root files

| File | Needed? | What it does and why |
|---|---|---|
| `AGENTS.md` | Yes | Defines the product principles, scope, evaluation rules, and definition of done for contributors. |
| `README.md` | Yes | Primary setup and usage guide. Correct its claim that PDF Storage policies are already migrated. |
| `CODEBASE_GUIDE.md` | Yes | Concise repository map. Correct the same Storage-policy claim. |
| `FILE_AUDIT.md` | Yes | This inventory and cleanup decision record. |
| `pyproject.toml` | Yes | Authoritative Python dependencies. The large PDF/OCR stack is intentional; local embedding/reranker packages are absent. |
| `uv.lock` | Yes | Reproducible locked Python dependency graph used by local setup, CI, and Docker. |
| `.python-version` | Yes | Keeps local Python aligned with Python 3.12 in the project and image. |
| `.env.example` | Yes | Safe template for database, hosted models, tracing, pool, CORS, and logging configuration. |
| `.env` | Local only | Holds secrets and local overrides. Refresh its stale Chroma/provider/device keys from `.env.example`, preserving secrets. |
| `.gitignore` | Yes | Prevents secrets, books, databases, caches, build output, and routine evaluation runs from being committed. |
| `.dockerignore` | Yes | Protects and reduces the backend Docker build context. |
| `Dockerfile` | Yes | Builds the FastAPI image with the full PDF parser, Poppler, OCR, and only active backend modules. |
| `docker-compose.yml` | Yes | Runs the API and web app locally with health gating and managed/local database URL support. |
| `app.py` | No; already deleted | Retired Gradio UI. Next.js plus FastAPI replaced it and Gradio is no longer installed. |

## API and PDF parsing

| File | Needed? | What it does and why |
|---|---|---|
| `api/__init__.py` | Yes | Explicit package boundary for the HTTP layer. |
| `api/main.py` | Yes | Production health, chat, SSE streaming, CORS, startup, and database-readiness endpoints. |
| `parsing/__init__.py` | Yes | Explicit package boundary for ingestion code. |
| `parsing/models.py` | Yes | Lossless parsed-book, section, text, table, and image contracts used by ingestion and storage. |
| `parsing/parser.py` | Yes | Extracts TOC and high-resolution PDF content. Future upload jobs must pass per-upload cache paths; shared defaults are not concurrency-safe. |

## Retrieval

| File | Needed? | What it does and why |
|---|---|---|
| `retrieval/__init__.py` | Yes | Public package exports for retrieval contracts and operations. |
| `retrieval/chunking.py` | Yes | Deterministically builds token-bounded chunks with exact citation provenance. |
| `retrieval/langchain.py` | Yes | Adapts explicit retrieval to LangChain and caches hosted provider clients. |
| `retrieval/models.py` | Yes | Shared chunk and provenance data contracts. |
| `retrieval/postgres.py` | Yes | Stores derived chunks and provides the active Postgres FTS/BM25 baseline. |
| `retrieval/reranker.py` | Yes | Calls and validates the hosted OpenRouter reranking API. |
| `retrieval/search.py` | Yes | Selects BM25, vector, hybrid RRF, or hosted-reranked retrieval. |
| `retrieval/vector.py` | Yes | Calls hosted embeddings, synchronizes pgvector rows, and performs exact cosine search. |
| `retrieval/sqlite.py` | No; deleted | Retired SQLite FTS implementation; no active caller and excluded from the image. |
| `retrieval/schema.sql` | No; deleted | Schema used only by the retired retrieval SQLite module. Supabase migrations are authoritative. |

## Canonical storage

| File | Needed? | What it does and why |
|---|---|---|
| `storage/__init__.py` | Yes | Explicit package boundary and storage-layer documentation. |
| `storage/database.py` | Yes | Database configuration, pooling, pgvector registration, owner scope, and readiness checks. |
| `storage/postgres.py` | Yes | Transactionally validates, imports, and restores canonical book content in Postgres. |
| `storage/sqlite.py` | No; deleted | Legacy canonical SQLite storage removed after migration validation. |
| `storage/schema.sql` | No; deleted | Legacy schema removed with the SQLite storage module. |

## Study workflow

| File | Needed? | What it does and why |
|---|---|---|
| `study/__init__.py` | Yes | Explicit package boundary for the study service. |
| `study/analyze.py` | Yes | Chooses conversational routes and standalone queries using deterministic rules plus a hosted control model. Review gold-set-specific fallbacks later. |
| `study/content.py` | Yes | Loads complete ordered canonical blocks for chapter and section summaries. |
| `study/context.py` | Yes | Builds auditable evidence context with citation markers and token metadata. |
| `study/contracts.py` | Yes | Strict shared models for state, decisions, scope, evidence, citations, and results. |
| `study/conversation.py` | Yes | Coordinates decisions, graph execution, and conversation state updates. |
| `study/graph.py` | Yes | Defines the inspectable LangGraph plan, route, execute, and update workflow. |
| `study/query.py` | Yes | Runs hierarchy operations and grounded retrieval QA with citation and abstention checks. |
| `study/render.py` | Yes | Deterministically renders hierarchy outlines without an unnecessary model call. |
| `study/request.py` | Yes | Parses explicit study commands and resolves their scope. |
| `study/scope.py` | Yes | Resolves books, chapters, and sections from canonical hierarchy rows. |
| `study/scope_candidates.py` | Yes | Restricts the control model to deterministic canonical scope candidates. |
| `study/streaming.py` | Yes | Normalizes model invocation and token streaming for API and study operations. |
| `study/summarize.py` | Yes | Builds complete-scope prompts, validates citations, and repairs invalid summaries. |

## Scripts

| File | Needed? | What it does and why |
|---|---|---|
| `scripts/__init__.py` | Yes | Enables documented `python -m scripts...` commands. |
| `scripts/parse_book.py` | Yes | Manually exercises PDF parsing before the upload API exists; currently points at one sample source. |
| `scripts/import_book.py` | Yes | Validates cached parser output and imports it into canonical Postgres. |
| `scripts/build_chunks.py` | Yes | Rebuilds derived chunks and lexical search data. |
| `scripts/build_vector_index.py` | Yes | Rebuilds hosted embeddings in pgvector. |
| `scripts/ask_book.py` | Yes | Manual routed grounded-QA CLI using the production query path. |
| `scripts/study.py` | Yes | Manual hierarchy and complete-scope summary CLI. |
| `scripts/inspect_scope.py` | Yes | Deterministic, non-LLM hierarchy/content diagnostic. |
| `scripts/evaluate_retrieval.py` | Yes | Measures BM25, vector, hybrid, and reranked retrieval. |
| `scripts/build_retrieval_report.py` | Yes | Builds the current managed-service retrieval comparison artifact. |
| `scripts/render_retrieval_report.py` | Yes | Deterministically renders the canonical retrieval artifact as self-contained HTML. |
| `scripts/evaluate_multiturn.py` | Yes | Replays selected or complete stateful conversation evaluations. |
| `scripts/build_multiturn_report.py` | Yes | Validates the gold set and rebuilds its searchable review page. |
| `scripts/validate_multiturn_gold.py` | Yes | Validates gold schema, ownership, nodes, pages, citations, and routes. |
| `scripts/migrate_sqlite_to_postgres.py` | No; deleted | One-time migration utility removed after the rollback window closed. |

## Evaluation code and artifacts

| File | Needed? | What it does and why |
|---|---|---|
| `evals/__init__.py` | Yes | Explicit evaluation package boundary. |
| `evals/judge.py` | Yes | Optional structured hosted judge for answer quality; it is not a safety gate. |
| `evals/multiturn.py` | Yes | Replays conversations and scores routing, evidence, citations, and outcomes. |
| `evals/report.py` | Yes | Creates searchable, inspectable HTML run reports. |
| `evaluation/README.md` | Yes | Documents evaluation methodology, commands, provenance, and limitations. |
| `evaluation/multiturn_gold.json` | Yes | Active frozen multi-turn gold set. |
| `evaluation/retrieval_gold_seed.json` | Yes | Active retrieval benchmark questions and expected evidence. |
| `evaluation/retrieval_comparison_artifact.json` | Yes | Current Postgres/pgvector/hosted-provider retrieval evidence. |
| `evaluation/multiturn_gold.html` | Yes; generated | Rebuilt from the active JSON gold set and canonical Postgres. |
| `evaluation/retrieval_comparison.html` | Yes; generated | Rebuilt from the current Postgres/pgvector/hosted-provider artifact. |

## Tests

| File | Needed? | What it does and why |
|---|---|---|
| `tests/__init__.py` | Yes | Test package marker used by shared helper imports. |
| `tests/fixtures.py` | Yes | Shared deterministic parsed-book fixture without a storage-backend dependency. |
| `tests/postgres.py` | Yes | Creates isolated Postgres owners and cleans integration-test data. |
| `tests/test_api.py` | Yes | Covers health readiness, request validation, chat, and SSE streaming. |
| `tests/test_conversation.py` | Yes | Covers follow-ups, state changes, abstention, scope, and LangSmith trace grouping. |
| `tests/test_hosted_providers.py` | Yes | Protects managed model defaults and embedding/reranker request contracts. Add this currently untracked file. |
| `tests/test_multiturn_evaluation.py` | Yes | Covers scoring, state replay, failure capture, and safe report rendering. |
| `tests/test_multiturn_gold.py` | Yes | Validates the frozen gold set against Postgres and report generation. |
| `tests/test_query_routing.py` | Yes | Covers hierarchy routing, retrieval fallback, summary repair, and insufficient evidence. |
| `tests/test_retrieval.py` | Yes | Covers Postgres chunks, FTS, vectors, hybrid retrieval, and reranking. |
| `tests/test_scope_candidates.py` | Yes | Covers canonical scope candidate ranking and structured decision contracts. |
| `tests/test_postgres_storage.py` | Yes | Covers lossless round trips, hierarchy, replacement, validation rollback, and cascade deletion in canonical Postgres. |
| `tests/test_storage.py` | No; deleted | Retired SQLite tests; shared fixtures and lifecycle coverage were moved first. |
| `tests/test_study.py` | Yes | Covers hierarchy resolution, content loading, ambiguity, and diagnostics. |
| `tests/test_study_summary.py` | Yes | Covers complete context, citation validation/repair, budgets, and rendering. |
| `tests/test_turn_analysis.py` | Yes | Covers follow-up interpretation, clarification, retries, bounded context, and scope switching. |

## Frontend

| File | Needed? | What it does and why |
|---|---|---|
| `frontend/app/page.jsx` | Yes | Stateful chat UI with SSE, retrieval controls, book selection, errors, and diagnostics. |
| `frontend/app/globals.css` | Yes | Responsive and accessible styling imported by the root layout. |
| `frontend/app/layout.jsx` | Yes | Required Next.js root layout and metadata. |
| `frontend/public/favicon.svg` | Yes | Favicon referenced by layout metadata. |
| `frontend/next.config.mjs` | Yes | Proxies `/api`, supports standalone builds, and preserves SSE behavior. |
| `frontend/package.json` | Yes | Frontend scripts, direct dependencies, and build/security configuration. |
| `frontend/package-lock.json` | Yes | Exact dependency graph required by `npm ci`. |
| `frontend/Dockerfile` | Yes | Non-root standalone Next.js production image. |
| `frontend/.env.example` | Yes | Documents server-side `BACKEND_URL` configuration. |
| `frontend/.dockerignore` | Yes; fix | Protects build context, but must also exclude `.env*` while allowing `.env.example`. |

## Documentation

| File | Needed? | What it does and why |
|---|---|---|
| `docs/architecture.md` | Yes | Current system, data, ownership, retrieval, and workflow architecture. Mark source-PDF Storage as planned. |
| `docs/model-selection.md` | Yes | Dated decision record for hosted generation, control, and judge models. Revalidate prices/benchmarks before changing models. |
| `docs/supabase-migration-plan.md` | Yes | Historical cutover decision record plus the current recovery and deferred Auth/upload plan. |

## CI and Supabase

| File | Needed? | What it does and why |
|---|---|---|
| `.github/workflows/ci.yml` | Yes | Runs Postgres-backed tests, frontend audit/build, API image build, parser/OCR checks, and retired-module absence checks. |
| `supabase/config.toml` | Yes | Reproducible local Supabase ports, services, migrations, and seed configuration. |
| `supabase/migrations/20260722185652_initial_study_partner.sql` | Yes | Authoritative pgvector, canonical/derived table, index, RLS, and grant schema. |
| `supabase/migrations/20260722185657_bootstrap_owner.sql` | Temporary | Fixed single-user owner for the current hosted mode. Replace after request-derived Auth ownership and data migration. |
| `supabase/seed.sql` | Yes | Deterministic local/CI bootstrap owner for database resets. |
| `supabase/deferred/book_sources_storage.sql` | Yes; deferred | Future private PDF bucket and owner policies. Promote to a timestamped migration when upload is implemented; it is not active today. |

## Ignored and generated areas

| Path | Needed? | Decision |
|---|---|---|
| `.venv/` | Local only | Rebuild with `uv sync --frozen`; never commit. |
| `frontend/node_modules/` | Local only | Rebuild with `npm ci`; never commit. |
| `frontend/.next/` | No | Generated frontend build output. |
| `__pycache__/`, `.pytest_cache/`, `.ruff_cache/` | No | Generated caches; safe to delete. |
| `cache/` | No as source | Rebuildable parser output. Future uploads need isolated per-job caches. |
| `sources/books/` | Local only | Input PDFs; keep private and move future uploads to managed object storage. |
| `outputs/` | No as source | Generated summaries and demonstrations. Publish only selected examples intentionally. |
| `evaluation/runs/` | No as source | Reproducible routine reports; remove intermediate `smoke*` runs or archive selected evidence outside Git. |
| `data/books.sqlite3*` | No; deleted | Pre-cutover rollback input removed after source-hash and canonical-count parity checks. |
| `data/retrieval.sqlite3` | No; deleted | Retired derived SQLite retrieval database. |
| `data/chroma/` | No; deleted | Retired local vector store. pgvector is active. |
| `.vscode/browse.vc.db*` | No | Generated editor database. |
| `.vscode/settings.json`, `.vscode/c_cpp_properties.json` | Local only | Personal editor configuration unless the team deliberately standardizes it. |
| `supabase/.temp/`, `supabase/.branches/` | No | Generated Supabase CLI state. |

## Recommended actions

1. Keep the full PDF parser in production.
2. Before adding concurrent uploads, give each job unique parser cache paths and
   promote `supabase/deferred/book_sources_storage.sql` into a reviewed migration.
3. Centralize the embedding model, dimension, and document-format constants used
   by indexing and readiness checks; also define `RetrievalMode` once.
4. Correct the Storage-policy wording in the README, codebase guide, and
   architecture document; harden `frontend/.dockerignore` against local env files.
5. Refresh the ignored local `.env` from `.env.example` while preserving secrets.
