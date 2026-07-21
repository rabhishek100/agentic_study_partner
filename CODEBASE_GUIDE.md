# Codebase guide

This repository is organized as a pipeline:

`PDF -> parsed book -> canonical SQLite -> rebuildable retrieval indexes -> study workflow -> FastAPI -> Next.js/CLI/Gradio -> evaluations`

The labels below mean:

- **Core** — required by the current application or its data pipeline.
- **Supporting** — useful for development, evaluation, reproducibility, or explanation, but not required by the running UI.
- **Generated/local** — useful output or machine state that can be recreated and normally should not be edited or committed.
- **Cleanup candidate** — not used by the active implementation or superseded; confirm before deleting.

## Root files

| File | What it does | Need? |
|---|---|---|
| `app.py` | Starts the optional Gradio chat UI and keeps per-session conversation state. | **Supporting** alternative to the Next.js client; useful for quick Python-only demos. |
| `README.md` | Setup, architecture, commands, current metrics, and usage documentation. | **Supporting and important** for the portfolio. |
| `AGENTS.md` | Records the project's goals, scope boundaries, and engineering principles. | **Supporting and important** for making consistent design decisions. |
| `pyproject.toml` | Defines the Python project and its dependencies. | **Core** for installation and execution. |
| `uv.lock` | Pins the complete dependency graph. | **Core** for reproducible environments. |
| `.python-version` | Selects the intended Python version for compatible tools. | **Supporting** and worth keeping. |
| `.env.example` | Documents model, tracing, token-budget, and local-model settings without secrets. | **Core** configuration template. |
| `.env` | Holds local secrets and overrides. | **Generated/local**; needed for real model calls and must never be committed. |
| `.gitignore` | Prevents secrets, source books, databases, caches, and routine outputs from being committed. | **Core** repository hygiene. |
| `Dockerfile`, `docker-compose.yml` | Build and run the FastAPI and Next.js services while mounting local book data. | **Supporting and important** for reproducible local setup. |
| `.dockerignore` | Keeps secrets, local data, source books, caches, and frontend dependencies out of Docker images. | **Core** Docker safety and build hygiene. |

## `api/` — HTTP boundary

| File | What it does | Need? |
|---|---|---|
| `main.py` | Provides health and chat endpoints, validates requests, and runs the existing study workflow without blocking other web requests. | **Core** backend for the Next.js client. |
| `__init__.py` | Package marker. | **Supporting**. |

## `frontend/` — Next.js React interface

| Path | What it does | Need? |
|---|---|---|
| `app/page.jsx` | Implements the client-side chat, settings, conversation state, errors, and latest-turn details. | **Core** browser interface. |
| `app/layout.jsx` | Defines the shared HTML layout and page metadata. | **Core** Next.js entry point. |
| `app/globals.css` | Responsive visual styling for the interface. | **Core** presentation. |
| `package.json`, `package-lock.json` | Define and lock frontend dependencies and commands. | **Core** reproducible frontend setup. |
| `next.config.mjs` | Forwards `/api` calls to FastAPI and creates a standalone server build when Docker requests one. | **Core** Next.js configuration. |
| `.env.example` | Documents the server-only FastAPI address used by the Next.js proxy. | **Supporting and important** deployment configuration. |
| `Dockerfile` | Builds and runs the standalone Next.js server as a non-root user. | **Supporting and important** for Docker setup. |

## `parsing/` — PDF to structured book

| File | What it does | Need? |
|---|---|---|
| `models.py` | Pydantic models for books, sections, text, tables, and images. | **Core** contract between parsing and storage. |
| `parser.py` | Reads the PDF TOC, extracts content with Unstructured, assigns it to sections, and caches the result. | **Core** ingestion stage. |
| `__init__.py` | Marks the directory as a Python package. | **Supporting**; tiny but conventional. |

## `storage/` — canonical, lossless data

| File | What it does | Need? |
|---|---|---|
| `schema.sql` | Defines books, hierarchy nodes, ordered content blocks, tables, and images. | **Core** source-data schema. |
| `sqlite.py` | Creates, validates, ingests, reads, and restores canonical book data transactionally. | **Core** persistence implementation. |
| `__init__.py` | Package marker. | **Supporting**. |

This layer is deliberately separate from retrieval. It is the durable truth from which chunks and indexes are rebuilt.

## `retrieval/` — rebuildable search data and ranking

| File | What it does | Need? |
|---|---|---|
| `models.py` | Defines chunk configuration, chunk provenance, and chunk records. | **Core** retrieval contracts. |
| `chunking.py` | Deterministically turns canonical blocks into bounded, citation-aware chunks. | **Core** index-building logic. |
| `schema.sql` | Defines chunk/build provenance tables and the SQLite FTS5 index. | **Core** BM25 storage schema. |
| `sqlite.py` | Atomically rebuilds chunks and performs BM25 search. | **Core** baseline retrieval. |
| `vector.py` | Builds and searches the local Chroma semantic index using a pinned embedding model. | **Core to the current default runtime** because `hybrid` retrieval uses both BM25 and vector search. Only a deliberately BM25-only setup can omit it. |
| `search.py` | Exposes BM25, vector, reciprocal-rank-fusion hybrid, and reranked retrieval modes. | **Core** shared retrieval API. |
| `reranker.py` | Scores a bounded hybrid shortlist with a pinned cross-encoder. | **Core module for the current code structure** because `search.py` imports it and exposes `hybrid_rerank`; the reranking strategy itself is optional and is not the default because evaluation showed no Recall@5 gain over plain hybrid. |
| `langchain.py` | Adapts the explicit retrieval strategies to LangChain's retriever interface. | **Core** because `study/query.py` uses it. |
| `__init__.py` | Re-exports the public retrieval API. | **Supporting and useful**. |

## `study/` — grounded requests and agentic orchestration

| File | What it does | Need? |
|---|---|---|
| `contracts.py` | Strict Pydantic contracts for conversation state, routing decisions, evidence, citations, and results. | **Core** safety boundary. |
| `scope.py` | Resolves books, chapters, named sections, and complete subtrees from canonical SQLite. | **Core** hierarchy-aware behavior. |
| `scope_candidates.py` | Produces deterministic likely scope matches for conversational routing. | **Core** routing support. |
| `content.py` | Loads all ordered canonical evidence for a resolved scope. | **Core** complete-scope summaries. |
| `context.py` | Formats scope evidence and measures its token size without silently truncating it. | **Core** grounded summarization. |
| `summarize.py` | Prompts for summaries, validates coverage/citations, repairs once, and appends references. | **Core** summary quality gate. |
| `request.py` | Parses explicit chapter/section list and summary commands deterministically. | **Core** deterministic-first routing. |
| `render.py` | Formats hierarchy outlines for section-list requests. | **Core but small** presentation helper. |
| `query.py` | Shared router/executor for hierarchy requests and retrieval QA, including grounded answer validation. | **Core** application service. |
| `analyze.py` | Handles obvious turns deterministically and uses one structured model decision for ambiguous conversational turns. | **Core** conversation planning. |
| `conversation.py` | Executes a turn, records messages/evidence, and manages active scope. | **Core** state coordinator. |
| `graph.py` | Defines the inspectable LangGraph flow: plan, route, execute, and update state. | **Core** agentic workflow and tracing boundary. |
| `__init__.py` | Package marker. | **Supporting**. |

## `scripts/` — command-line operations

These are thin operational entry points. They are not needed merely to import the application, but they are important for rebuilding, inspecting, evaluating, and demonstrating it.

| Files | What they do | Need? |
|---|---|---|
| `parse_book.py`, `import_book.py` | Parse the current PDF and import cached parser output into canonical SQLite. | **Core pipeline tools**. `parse_book.py` is currently tied to the example book path. |
| `build_chunks.py`, `build_vector_index.py` | Rebuild BM25 chunks and synchronize the semantic index. | **Core pipeline tools**. |
| `inspect_scope.py` | Inspect resolved hierarchy and canonical content without an LLM call. | **Supporting and very useful** for debugging. |
| `study.py` | Specialized CLI for explicit hierarchy queries, prompt-budget dry runs, and validated full-scope summaries. | **Supporting and useful** even though some behavior overlaps the shared query CLI. |
| `ask_book.py` | Small CLI over the same routed query handler used by the app. | **Supporting** primary end-to-end CLI. |
| `evaluate_retrieval.py`, `evaluate_multiturn.py` | Run retrieval and stateful-conversation evaluations. | **Core to the project's evaluation goal**. |
| `build_retrieval_report.py`, `build_multiturn_report.py` | Turn evaluation data into inspectable, self-contained reports. | **Supporting and portfolio-useful**. |
| `validate_multiturn_gold.py` | Checks the gold set against canonical nodes, pages, routes, and state contracts. | **Core to trustworthy evaluation**. |
| `__init__.py` | Allows commands to run as `python -m scripts...`. | **Supporting**. |

## `evals/` — reusable evaluation logic

| File | What it does | Need? |
|---|---|---|
| `multiturn.py` | Replays conversations and calculates deterministic turn/evidence metrics. | **Core to multi-turn evaluation**. |
| `judge.py` | Optional OpenRouter semantic answer judge. | **Optional**; useful for qualitative scoring but not a safety gate. |
| `report.py` | Renders multi-turn run results as HTML. | **Supporting and useful**. |
| `__init__.py` | Evaluation package marker/exports. | **Supporting**. |

## `evaluation/` — gold data and report artifacts

| Files | What they do | Need? |
|---|---|---|
| `retrieval_gold_seed.json` | Frozen retrieval questions and expected evidence. | **Core evaluation input**; edit only through reviewed dataset changes. |
| `multiturn_gold.json` | Frozen 44-turn routing/state/evidence dataset. | **Core evaluation input**; currently model-adjudicated, not human-verified. |
| `README.md` | Explains dataset provenance, scoring, and limitations. | **Supporting and important**. |
| `retrieval_comparison_artifact.json`, `retrieval_comparison.html` | Rebuildable detailed four-strategy results and human-readable report. | **Supporting evidence** worth keeping for the portfolio. |
| `multiturn_gold.html` | Rebuildable review view of the multi-turn gold set. | **Supporting generated report**. |
| `runs/` | Timestamped routine evaluation outputs. | **Generated/local** and correctly ignored. |

## `tests/` — deterministic safeguards

The test files cover the HTTP API, storage round-trips, retrieval and ranking, scope matching, explicit query routing, summaries and citation coverage, conversation state, turn analysis, and evaluation scoring. They are **supporting but essential for a production-shaped project**. `tests/__init__.py` is only a package marker. Test bytecode and `.pytest_cache/` are disposable generated files.

## Documentation and local artifact folders

| Path | What it contains | Need? |
|---|---|---|
| `docs/model-selection.md` | Dated cost/capability rationale for the three model roles. | **Supporting and portfolio-useful**; revisit when models or prices change. |
| `docs/architecture.md` | Mermaid component diagram plus the important source/derived-data boundaries. | **Supporting and portfolio-important**. |
| `.github/workflows/ci.yml` | Runs the data-independent Python tests, dependency audit, and Next.js production build on pushes and pull requests. Gold-set checks that require the ignored local book database still run locally. | **Supporting and important** quality gate. |
| `sources/books/` | Original copyrighted PDFs. | **Local input** needed to reproduce ingestion, but intentionally ignored and not distributable by default. |
| `cache/` | Unstructured extraction and parsed-book JSON caches. | **Generated/local**; useful for faster rebuilds, safe to regenerate. |
| `data/books.sqlite3` | Populated canonical book database. | **Required local runtime data**; canonical after ingestion but not source code. |
| `data/retrieval.sqlite3`, `data/chroma/` | BM25 chunks/FTS and vector data. | **Generated/local**; always rebuildable from `books.sqlite3`. |
| `data/*.sqlite3-wal`, `data/*.sqlite3-shm` | SQLite live transaction sidecars. | **Generated/local**; never source files and should only be removed after database processes close. |
| `outputs/` | Manually requested summaries or context dumps. | **Generated/local**; useful demo material, not required by runtime. |
| `.venv/`, `__pycache__/`, `.pytest_cache/` | Installed environment and Python/test caches. | **Generated/local** and disposable. |

## Maintenance notes

- The old BM25-only reports were removed because the four-way comparison contains the same baseline plus the later retrieval strategies.
- Docker expects populated local data under `data/`; ingestion remains an explicit step so copyrighted books and generated databases are not baked into images.
- PyTorch is locked to its official CPU-only package index, matching the current embedding/reranking configuration and keeping local and Docker installs free of unused CUDA libraries.
- Empty `__init__.py` files contain little logic, but keeping them makes package boundaries explicit and avoids tooling/import surprises.
