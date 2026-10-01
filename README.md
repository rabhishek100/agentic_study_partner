# Agentic Study Partner

An evidence-first workspace for technical books, papers, and lectures. It
produces cited answers, complete-scope explanations, flashcards, revision
sheets, and interview practice. Source material stays separate from generated
study artifacts.

## Features

| Feature | Behavior | Flow |
|---|---|---|
| Books and papers | Parse digital PDFs; transcribe scanned books with outline review | [PDF ingestion](docs/ingestion.md#pdf-ingestion) |
| Lectures and courses | Combine captions/audio, frames, visual analysis, and supporting PDFs | [Video ingestion](docs/ingestion.md#video-ingestion) |
| Questions | Search selected sources; cite pages or timestamps; optionally use external answers | [Questions](docs/flows.md#questions) |
| Reading and side chats | Ask about an open page, chapter, selected passage, or previous answer | [Reading](docs/flows.md#reading-and-side-chats) |
| Chapter explanation | Explain the complete scope with citations and coverage checks | [Summaries](docs/flows.md#complete-summaries) |
| Verbatim reading | Display parsed source text in ordered installments | [Verbatim](docs/flows.md#verbatim-reading) |
| Flashcards and reminders | Create cited cards, schedule reviews, and notify when a daily queue is available | [Flashcards](docs/flows.md#flashcards) |
| Revision sheets | Generate downloadable sheets with figure, citation, and quality checks | [Revision sheets](docs/flows.md#revision-sheets) |
| Interviews | Run adaptive, graded sessions or play generated ideal exchanges | [Interviews](docs/flows.md#interviews) |
| Read-aloud | Speak answers or source passages; optionally interrupt with a voice question | [Audio](docs/flows.md#read-aloud) |

## System

```mermaid
flowchart TD
    UI[Next.js workspace] --> API[FastAPI]
    API --> G[Study graphs]
    API --> W[Queued worker jobs]
    W --> D[(Postgres and private files)]
    G --> R[Retrieve source evidence]
    R --> D
    G --> M[Hosted models]
    G -. traces .-> L[LangSmith]
    API -. traces .-> L
    W -. traces .-> L
    M -. traces .-> L
```

Ingestion uses deterministic Python. LangGraph coordinates study decisions
and bounded repairs. The default book search is hybrid retrieval with
reranking: Recall@5 improved from 88.9% to 100% on 12 answerable evaluation
questions. This is a small, source-specific result; see [evaluation](docs/evaluation.md).

## Documentation

The main reading order is architecture → ingestion → study flows → design
decisions → evaluation. Detailed setup and transport behavior live in the
reference guides.

| Guide | Contents |
|---|---|
| [Architecture](docs/architecture.md) | Stack, storage, retrieval, authentication, reliability |
| [LangGraph workflows](docs/langgraph.md) | Graphs exported from compiled workflows, state, routing, regeneration |
| [Database schema](docs/database.md) | Table inventory, core relationships, ownership, migration reference |
| [API reference](docs/api.md) | Swagger/ReDoc, JWT authorization, request examples, generated endpoint catalog |
| [Ingestion](docs/ingestion.md) | Digital books, OCR and human review, papers, multimodal video |
| [Study flows](docs/flows.md) | Questions, reading, generated artifacts, interviews, audio |
| [Design decisions](docs/design-decisions.md) | Model defaults, selection rationale, tradeoffs |
| [Evaluation](docs/evaluation.md) | Measurements, datasets, failed experiments, limits |
| [Interface](docs/interface.md) | Rendering and data fetching, screens, interaction rules, accessibility |
| [Interview voice](docs/interview-voice.md) | LiveKit and HTTP speech transport, recovery, privacy |
| [Operations](docs/operations.md) | Setup, configuration, workers, deployment, CI |

[Contributing](CONTRIBUTING.md) covers change policy;
[Security](SECURITY.md) covers security boundaries. The running API exposes
interactive Swagger UI at `/docs`, ReDoc at `/redoc`, and the OpenAPI contract
at `/openapi.json`; see the [API guide](docs/api.md).

## Run locally

Prerequisites: Docker, Node.js 24, `npm`, `uv`, and an OpenRouter API key.

```bash
cp .env.example .env
# Set OPENROUTER_API_KEY in .env
scripts/local.sh setup
scripts/local.sh up
scripts/local.sh doctor
```

Web: `http://localhost:3000`; API health: `http://localhost:8000/api/health`.
`scripts/local.sh down` stops services without deleting data.

## Verify changes

Repository-wide LangSmith setup, trace organization and a no-spend hosted
delivery check: [observability](docs/observability.md).
Test-suite audit, cleanup and prioritized gaps: [test audit](docs/test-suite-audit.md).
Free Grafana operational monitoring and PostHog UI analytics:
[setup and coverage](docs/operational-observability.md).

```bash
uv sync --frozen
# Use a migrated test database, separate from the application corpus/queues.
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/study_partner_eval_test \
  uv run --frozen --extra voice python -m pytest tests -q
cd frontend
npm ci
npm run typecheck
npm test
npm run build
```

Provision the test database once, then run
`scripts.bootstrap_postgres --url-env TEST_DATABASE_URL` to apply the schema.
CI provides an empty migrated Supabase instance. Local Storage integration tests
also need loopback `SUPABASE_URL` and its local development service-role key;
missing Storage/corpus fixtures are reported as skips. Pytest keeps hosted media,
provider keys and telemetry out of ordinary tests, and skips global queue tests
when the selected database already has live jobs. See the
[isolation verification](docs/test-suite-audit.md#test-isolation-and-baseline-triage).

## Code map

| Path | Responsibility |
|---|---|
| `api/` | Authentication, HTTP contracts, streaming |
| `observability.py` | Shared LangSmith HTTP/workflow/provider boundaries and context propagation |
| `ingestion/`, `parsing/`, `storage/` | PDF parsing, canonical storage, durable jobs |
| `retrieval/`, `study/` | Search, grounding, book/paper conversations |
| `video/` | Lecture/course ingestion and multimodal study |
| `decks/`, `notifications/`, `revision_sheets/` | Cards, reviews, reminders, sheets |
| `interviews/`, `narration/` | Interview reasoning, speech, read-aloud |
| `evals/`, `evaluation/` | Evaluation code, datasets, result artifacts |
| `frontend/` | Next.js/React interface |
| `supabase/migrations/`, `tests/` | Schema, ownership constraints, automated checks |

## License

No license has been selected. All rights reserved; reuse or redistribution
requires permission.
