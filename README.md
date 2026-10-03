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
    API -. OTLP .-> O[Grafana logs traces metrics]
    W -. OTLP .-> O
    UI -. safe events .-> P[PostHog]
```

Ingestion uses deterministic Python. LangGraph coordinates study decisions
and bounded repairs. LangSmith traces every graph, model and retrieval step;
Grafana Cloud holds operational logs, traces and metrics; PostHog records
privacy-filtered interface events.

Evaluation drives the design:

- **Retrieval.** Hybrid search with reranking raised book Recall@5 from 88.9%
  (BM25) to 100% on 12 answerable gold questions.
- **Five-flow quality.** A 54-case suite covers chat, complete summaries,
  lecture/course study, revision sheets and interviews, judged against source
  evidence. Measured fixes raised usable outputs from 24/45 to 34/45 on the same
  cases.
- **Rejected changes.** Of twenty further single-change candidates, including
  cheaper models and retrieval tweaks, only larger sheet citations survived
  repeats and blinded review.
- **Cost.** A light month of use is forecast at about $1.

These are small, source-specific results with an uncalibrated LLM judge; see
[evaluation](docs/evaluation.md).

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
| [Evaluation](docs/evaluation.md) | Method, datasets, results, kept and rejected experiments, cost, harness, limits |
| [Observability](docs/observability.md) | LangSmith AI traces, Grafana logs/traces/metrics, PostHog usage, request correlation |
| [Interface](docs/interface.md) | Rendering and data fetching, screens, interaction rules, accessibility |
| [Interview voice](docs/interview-voice.md) | LiveKit and HTTP speech transport, recovery, privacy |
| [Operations](docs/operations.md) | Setup, configuration, workers, recovery, testing, deployment, CI |

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

```bash
# Backend tests need a dedicated migrated database; see docs/operations.md#testing.
export TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/study_partner_eval_test
uv sync --frozen --extra voice
uv run --frozen --extra voice python -m pytest tests -q -ra
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
```

Test database setup, browser journeys and generated-reference checks are in
[operations](docs/operations.md#testing); paid quality evaluation is in
[evaluation](docs/evaluation.md#reproduce).

## Code map

| Path | Responsibility |
|---|---|
| `api/` | Authentication, HTTP contracts, streaming |
| `ingestion/`, `parsing/`, `storage/` | PDF parsing, canonical storage, durable jobs |
| `retrieval/`, `study/` | Search, grounding, book/paper conversations |
| `video/` | Lecture/course ingestion and multimodal study |
| `decks/`, `notifications/`, `revision_sheets/` | Cards, reviews, reminders, sheets |
| `interviews/`, `narration/` | Interview reasoning, speech, read-aloud |
| `evals/`, `evaluation/` | Evaluation harness and judges; datasets and sanitized results |
| `observability.py`, `operations_telemetry.py` | LangSmith boundaries; OpenTelemetry export and JSON logging |
| `model_routing.py` | Optional OpenRouter provider pinning and provider-specific structured-output hints |
| `frontend/` | Next.js/React interface |
| `supabase/migrations/`, `tests/` | Schema, ownership constraints, automated checks |
| `scripts/`, `ops/` | Local/deploy/evaluation commands; database, storage and dashboard configuration |

## License

No license has been selected. All rights reserved; reuse or redistribution
requires permission.
