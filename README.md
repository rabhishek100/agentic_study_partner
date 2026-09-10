# Agentic Study Partner

An evaluation-driven study companion for technical books. It parses PDFs into
a lossless hierarchical model, stores canonical and rebuildable retrieval data
in Supabase Postgres, and uses an inspectable LangGraph workflow to produce
grounded answers and complete-scope summaries with citations.

The application is **multi-user**. The browser signs in with Supabase Auth,
uploads PDFs resumably to private Storage, and polls durable job progress while
an asynchronous worker parses, imports, chunks, embeds, and verifies each book.
Every API request derives its owner from a verified access token. See
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
- Ordinary retrieval uses a small depth-aware budget: five chunks for quick
  answers and eight for interview/deep answers. System-design retrieval may
  retain several chunks from one hierarchy node because some parsers represent
  an entire design chapter as one node; concept retrieval keeps node diversity.

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

Bring up the complete local environment (Supabase Postgres, Auth, Storage,
API/worker, and web):

```bash
scripts/local.sh setup
scripts/local.sh up
scripts/local.sh doctor
```

The web app is at `http://localhost:3000`; `scripts/local.sh down` preserves
local data. See [`docs/environments.md`](docs/environments.md) for local and
staging operations, isolation rules, promotion, and rollback.

To replace the isolated local data with a verified, read-only snapshot of the
production library—without copying production Auth credentials or repeating
model-billed ingestion—run `scripts/clone_prod_to_local.sh --yes`. The full
contract and local-only login handling are documented in
[`docs/environments.md`](docs/environments.md#local-production-library-snapshot).

On the Supabase Free plan, staging uses a smaller deterministic fixture rather
than a second full production database. Refresh it with
`scripts/clone_prod_to_staging.sh --yes`; the selected documents and safety
contract are documented in
[`docs/environments.md`](docs/environments.md#curated-staging-snapshot).

PDF parsing dependencies remain part of the deployed FastAPI image so the
existing parser is available to the planned upload/ingestion API. Embedding
and reranking inference remain hosted and do not add local model dependencies.

### Without Docker

`supabase start` needs Docker. Where that is not available, a plain Postgres
builds the same schema:

```bash
brew install postgresql@17 pgvector
scripts/local_postgres.sh start
```

It creates a cluster on the same port, applies
[`supabase/local/supabase_shim.sql`](supabase/local/supabase_shim.sql) — the
handful of Supabase objects the migrations refer to — and then every migration
in order. `stop`, `status` and `reset` do what they say.

What this gets you is the schema, which is what the storage, schema, and
row-level-security tests talk to. What it does not get you is the Storage HTTP
API or the auth service, so the ingestion-pipeline and worker tests still need
the real thing: they upload PDFs to a bucket over HTTP. Everything else runs.

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
  -> upload to private Storage  resumable, 50 MB and application/pdf only
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

Or run both under one supervisor, the way the deployed service does:

```bash
uv run python -m scripts.serve
```

Local video uses `VIDEO_MEDIA_ROOT`. Production course media can instead use a
private S3-compatible Cloudflare R2 bucket with a disposable verified local
cache, so the API and worker do not depend on a paid shared media volume. See
[`docs/deployment.md`](docs/deployment.md).

## Video courses

The Courses area groups the existing canonical lecture records into an ordered
curriculum; it does not duplicate video files or run a second ingestion
pipeline. Create a course in `/courses` from one YouTube playlist URL, or paste
one lecture per line (optionally `Title | URL`). Each lecture enters the
ordinary video queue, and ready lectures can then be searched together from
the course workspace. Course
citations identify both the lecture and its timestamp or linked-document page.

Course creation and membership are also available at `/api/courses`; grounded
multi-lecture conversations live under `/api/course-conversations`. The full
contract and evaluation boundary are in
[`docs/video-course-feature-spec.md`](docs/video-course-feature-spec.md).

It claims one job at a time with `FOR UPDATE SKIP LOCKED`, holds a lease it
renews while working, and stops at a safe boundary on `SIGTERM`. A crashed
attempt is reclaimed once its lease expires and resumes from the canonical
import when one committed. Use `--once` to process a single job and exit.

Preflight measures text coverage, image coverage, full-page rasters, and OCR
text overlays before parsing. Embedded outlines are Unicode-normalized,
checked against their destination pages, and scored for hierarchy, coverage,
suspicious titles, and same-page collisions. Missing or unsafe native-digital
outlines receive a deterministic typography-based proposal for human review;
inferred headings are never accepted automatically.

Inspect one PDF or a directory without ingesting it:

```bash
uv run python -m scripts.inspect_pdf_outlines path/to/pdf-or-directory
```

The worker automatically accepts digital PDFs whose embedded outline is safe
after harmless title normalization. The parser consumes that exact approved
outline, so blank publisher rows can be discarded without reappearing during
extraction. A native-digital PDF with a missing or unsafe outline pauses in
`needs_toc_review`: the interface shows a deterministic typography-based
proposal whose levels, titles, and PDF pages must be confirmed or corrected
before the same job resumes. OCR-backed books remain blocked for the separate
OCR-quality workflow. When several sections begin on one page, ordered
extracted heading positions divide their content; ingestion fails safely if
every boundary cannot be resolved. Repeated margin text is retained in
canonical storage but omitted from retrieval and summary contexts.

Current limits, all configurable:

| Limit | Value |
|---|---:|
| Source object size | 50 MB |
| PDF pages | 1,000 |
| Pending jobs per user | 3 |
| Worker concurrency | 1 |



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

The reranker candidate Recall@20 is 1.000. Hybrid plus reranking is the
interactive default for new conversations; latency and cost remain visible in
traces so that choice can be revisited with production evidence.

Validate the knowledge-authored interview-answer seed:

```bash
uv run python -m scripts.validate_interview_dataset
```

The 30 cases span four production books, all three response depths, concept
answers, system-design walkthroughs, chapter reviews, follow-ups, and grounded
abstention. Most page mappings are candidate evidence anchors pending human
review, so the seed is suitable for diagnostic prompt comparisons but not yet
for a published quality claim.

To additionally check every book hash, canonical node, path, and page range
against Postgres:

```bash
uv run python -m scripts.validate_interview_dataset \
  --database-url "$MIGRATION_DATABASE_URL"
```

Run the three-case live smoke evaluation through the real coordinator:

```bash
uv run python -m scripts.evaluate_interview_answers \
  --smoke \
  --database-url "$MIGRATION_DATABASE_URL" \
  --judge-answers
```

After inspecting its JSON and HTML outputs under
`evaluation/runs/interview/`, run the frozen 30-case baseline by replacing
`--smoke` with `--all`. Generation, control, reranking, and optional judge
calls use the configured hosted models and therefore incur provider cost.

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
to FastAPI at `http://localhost:8000`, and talks to Supabase directly for
sign-in and resumable uploads. Copy `frontend/.env.example` to
`frontend/.env.local` first; the committed defaults match the local Supabase
CLI stack. Create an account, upload a PDF, watch the job progress in the
sidebar, and the book becomes selectable when verification finishes. Run the
worker (`uv run python -m worker.main`) alongside the API or uploads will
queue without being processed.

Chat defaults to an interview-preparation profile. Each answer is shaped as a
concept explanation, system-design walkthrough, or complete-scope interview
review, while the existing book-only grounding and citation rules remain
mandatory. The composer offers Quick answer, Interview answer, and Deep dive;
explicit wording such as “briefly” or “go deeper” overrides that selection for
the turn.

Prompt settings expose the locked grounding message, editable interview
instructions, archetype-specific response templates, the editable user-message
template, and a compiled preview. Owner defaults are stored in Postgres and
snapshotted when a conversation starts, so changing a default does not silently
change an existing study history. The answer inspector records the selected
archetype, resolved depth, routing reason, and prompt-profile version.

A **side chat** is a small conversation anchored to a passage of another one, so
an intermediate question can be asked beside the paragraph that prompted it
instead of at the bottom of the main thread. Highlight a sentence in an answer
and ask about it: the window floats over the conversation, moves, resizes and
minimizes to a dock, and several can answer at once — three at a time, with the
rest queued.

The passages a reader highlights are the side chat's priority context. What
their citation markers name — book chunks in a book chat, evidence units in a
lecture — is pinned to the front of that turn's evidence, so the answer rests on
the same source the quoted sentence did, while the quoted text itself is passed
as focus and never as something an answer may cite. Surrounding
main-conversation context is included under a token budget, and every turn
records what did not fit, which the answer inspector shows.

Both surfaces have them, sharing one window layer: a lecture side answer still
seeks the player and opens the slide it cites. A lecture's evidence belongs to
the published ingestion version that produced it, so a side turn pins only what
the version it retrieved from still contains and reports any anchor a re-ingest
replaced. See
[`docs/floating-side-chats-spec.md`](docs/floating-side-chats-spec.md).

Every composer — book chat, side chats, and the lecture ask pane — has a
microphone beside its send button. It records a question, posts the clip to
`POST /api/transcriptions`, and puts the words at the caret, where they are
edited and sent like anything typed; nothing is asked automatically, because
speech recognition mishears technical terms often enough that auto-sending
would spend a retrieval turn on a question nobody asked. Transcription reuses
`OPENROUTER_AUDIO_MODEL`, the same hosted model video ingestion falls back to,
and the audio is never stored. Recording stops on its own after two minutes,
Escape discards it, and the button hides itself where recording is impossible —
an insecure origin, or a browser without `MediaRecorder` — since every surface
stays fully usable by typing.

A caret beside the microphone chooses which input to record from, and appears
only once there is more than one. The choice is remembered across sessions and
shared by every composer on the page, since a headset picked in one is meant
for all of them. Browsers withhold device names until an origin has been
granted the microphone once, so the list is named after the first recording
rather than before it; Chrome's `default` and `communications` aliases are
dropped, because the menu has its own "System default" entry that means the
same thing. A device chosen and later unplugged does not silently demote to
the laptop lid: the request fails against the exact device, the stored choice
is dropped, and the recording restarts on the system default.

An opt-in LiveKit Cloud voice transport is under evaluation. Its feature flags,
worker setup, migration plan, and live validation checklist are documented in
[`docs/livekit-interview-migration.md`](docs/livekit-interview-migration.md).
The existing HTTP voice transport remains the default.

**Interviews** run an adaptive, source-grounded mock interview over one book
chapter or one lecture. Choose a 15, 30, 45, 60, 90, or 120 minute ceiling,
entry/mid/senior level, and realistic or guided feedback. The ceiling is not a
quota: a session closes early as soon as its substantive topic inventory is
covered, and strong answers complete a topic without filler follow-ups.

Before the first model call, the selected scope is inventoried deterministically
and classified as concept, system-design, or source-led interview material. The
LangGraph turn workflow evaluates against that topic's evidence, permits at
most one materially different diagnostic follow-up, optionally verifies a
material answer extension on the web, and then advances or closes. Recent
question similarity is validated before a generated question can reach the
candidate, so the interview cannot keep paraphrasing the same prompt.
Realistic mode withholds rubrics, detailed coaching, scores, and topic order
until the report while still giving a natural short reaction after each
answer; guided mode returns the detailed feedback after each turn. Reports retain the
transcript, six dimension scores, recommended answers, citations, cost, and
missed topics without inventing percentiles.

Voice answers listen continuously by default, with push-to-talk as a fallback.
The setup separates required microphone access from the optional live input
test, so candidates never need to prove input activity before starting. Session
capture
calibrates to that device's noise floor. Silence transcribes one segment into
the editable draft but never submits it;
the candidate explicitly sends the complete answer. New questions are spoken
automatically through `OPENROUTER_TTS_MODEL`. After each submission, the
interviewer writes and speaks a concise reaction before revealing and speaking
the next question. Clips are transcribed through
`OPENROUTER_INTERVIEW_STT_MODEL`; both remain usable as text if media access,
browser autoplay, or a provider call fails. Slow hosted TTS falls back to the
device voice rather than blocking the interview. On suitable primary questions,
the interviewer automatically asks the candidate to draw an architecture,
derive an equation, state assumptions and estimates, or write code/pseudocode.
Each turn is limited to one atomic objective; a screen instruction changes only
the response format and cannot append trade-offs, edge cases, testing, or other
subquestions. Compound drafts are rejected and regenerated before display.
That instruction appears in the question card and voice narration. Browser
privacy still requires one candidate click to choose a shared window; sharing
stays local until the candidate explicitly sends a still checkpoint. Raw audio and screen frames are
processed ephemerally and never stored; only transcripts and structured screen
observations survive. Explicit Pause stops the clock, while a refresh restores
the live question and voice controls instead of silently pausing the session.
Persisted paused sessions can be resumed from a prominent in-session control.
The workspace keeps one live activity panel visible throughout the turn. It
distinguishes source-grounded answer evaluation, next-turn planning, Whisper
transcription, question and feedback TTS, screen-checkpoint analysis,
pause/resume, and final-report loading; indeterminate work shows elapsed time
instead of a fabricated completion percentage.

The default interview stack is `openai/gpt-5.6-luna` for reasoning,
`openai/whisper-large-v3-turbo` for STT, and
`mistralai/voxtral-mini-tts-2603` with `en_paul_neutral` for TTS. See
[`docs/interview-session-spec.md`](docs/interview-session-spec.md)
for the complete product and evidence contract.

**Flashcards** turn one chapter, or one lecture, into cards you can review in a
few minutes a day. Pick a scope under **Cards** and generation runs as a durable
background job; the deck appears when it lands.

Coverage is a property of the deck, not a claim about it. Before any model call
the scope is inventoried in plain Python — the content-bearing nodes of a book
chapter, or `video.lecture.coverage_units` for a lecture — and that list is the
contract. Cards are generated per topic, so a call that can only see section 7.3
cannot write about the chapter introduction. Every card is then validated
deterministically: its citation markers must resolve inside its own topic, in
its declared citations *and* in its prose, or it is dropped and counted. Any
required topic left uncovered gets one targeted repair pass. What survives is
reported per deck — topics covered, cards dropped and why, type and priority
distributions — and shown in the interface.

Cards come in four shapes, chosen per topic: an interview question with a model
answer, a concept to recall, a multiple choice with distractors drawn from
genuinely confusable neighbours in the same evidence, and a system-design card
laid out as components, data flow, trade-offs and failure modes with the source
figure or lecture frame beside it. Each carries an interview-priority score, so
the deck keeps full coverage while the daily queue introduces what matters most
first. A card may also carry one **interview angle** — a follow-up an
interviewer would raise that the source does not cover — kept in its own field,
labelled as model knowledge, and excluded from every grounding metric.

Review is SM-2 with an ease floor and a halved lapse. The Today queue mixes
everything due across every deck with a capped number of new cards (ten by
default), and each grading button shows the gap it will schedule. A card you
cannot recall offers two things: open the cited page or lecture timestamp, or
carry it into a grounded conversation seeded with the card. See
[`docs/flashcard-decks-spec.md`](docs/flashcard-decks-spec.md).

**Revision sheets** are saved, cited two-page A4 reviews of one book chapter
or an entire paper. Choose **Revision sheet** beside the Q&A controls, select
a chapter or paper, then create or reopen a saved sheet. Each combines
original source figures, compact concepts, trade-offs/results, and recall cues.
Every original figure is inspected in batches and available in an expandable gallery. The
responsive reading view links citations to original pages and inspected figures;
**Download HTML**, **Download PDF** and **Print / A4 preview** share an owned,
script-free HTML template. The PDF is limited to two readable A4 pages.
**Ask about this** answers from the original source scope.

Generation runs in the existing worker and survives closing the dialog. It
builds an independent source concept inventory with an exact page ledger, checks citation locations
and page fit, then reviews the rendered pages against a four-part LLM rubric:
beauty, presentation, concept coverage and conciseness. Every dimension must
score at least 4/5 and every essential concept must be covered. Up to two
targeted review revisions are allowed, in addition to one schema/citation repair
and one fit repair. It fails rather than truncating an over-budget source or shrinking the
10.5-point body text. Reopening makes no model call; regeneration saves an
immutable new version. A failed/cancelled job leaves previous versions intact.

Apply migration `20260905120000_revision_sheets.sql` before restarting the API
and worker. Install the HTML renderer locally with `uv run playwright install chromium`;
the Docker image includes Chromium and its system dependencies. Generation uses
`OPENROUTER_REVISION_MODEL` when set (default `openai/gpt-5.6-luna` with low reasoning effort), with `OPENROUTER_API_KEY` and the existing
LangSmith configuration. `OPENROUTER_REVISION_JUDGE_MODEL` optionally selects
a separate reviewer model; by default it uses an independent call to the revision
model. `REVISION_CONTEXT_WINDOW_TOKENS` defaults to 64,000;
large scopes return an explicit limitation rather than silently dropping pages.

Reproduce a local source evaluation (IDs are installation-specific):

```bash
uv run python -m scripts.evaluate_revision_sheets --book-id BOOK_ID \
  --chapter-node-id CHAPTER_NODE_ID --output outputs/revision-evaluation/chapter
# Omit --chapter-node-id for an entire paper. --save exercises the durable
# worker path and saves under DEFAULT_OWNER_ID, processing only that job.
```

See the [feature specification](docs/revision-sheets-spec.md) and
[initial evaluation](docs/revision-sheets-evaluation.md). Citation identity and
coverage-map consistency are deterministic checks; semantic completeness still
requires source review. The initial evaluation covers two sources, with the
larger held-out comparison still pending.

The API exposes `GET /api/health`, `GET /api/books`,
`GET /api/books/{id}/chapters`, `POST /api/chat`,
`POST /api/chat/stream`, `POST /api/transcriptions`,
`GET/PATCH /api/prompt-settings`, prompt preview,
conversation CRUD, side chats (`POST|GET /api/conversations/{id}/side-chats`,
`PATCH /api/side-chats/{id}`, `POST /api/side-chats/{id}/turns/stream`), decks
(`POST|GET /api/decks`, `GET /api/decks/{id}`, `GET /api/decks/queue`,
`POST /api/decks/cards/{id}/review`, `GET|PATCH /api/decks/preferences`), the
interview lifecycle (`POST|GET /api/interviews`, preflight, start/resume/pause,
answers, finish, report, transcription, speech, and screen checkpoints), and the
`/api/ingestions` lifecycle. Everything except health requires a Supabase bearer
token.

Health reports infrastructure readiness only: it returns 503 when the canonical
or retrieval schema is missing, and stays healthy while a book is being
ingested. Per-book completeness is reported per book by `GET /api/books`.

Every study turn and LLM call can be traced in LangSmith when the standard
LangSmith environment variables are configured. LangSmith is not used as the
ingestion job database; Postgres is.

## Reading answers aloud

Every answer in the book conversation, in a side chat, and in the video
conversation carries a **Read aloud** control; the latest turn also offers
**Read exchange**, which reads the question first. Highlighting a passage
offers **Read this** beside **Ask about this**. Speed is adjustable from 0.75x
to 2x and is remembered per device.

What is spoken is what is on screen, with markdown, LaTeX and citation markers
turned into words rather than read out as syntax, and code blocks and tables
announced rather than recited. The one addition is diagrams: a cited figure is
described at the point the answer cites it, from a spoken description written
once per image by a vision model and stored in `narration_figures`.

Apply migration `20260906120000_read_aloud.sql` before restarting the API.
Speech uses `OPENROUTER_READING_TTS_MODEL` / `_VOICE` when set and otherwise
the interviewer's `OPENROUTER_TTS_*` pair; figure descriptions use
`OPENROUTER_NARRATION_FIGURE_MODEL`, falling back to the caption model.
Synthesised audio is cached per owner in `narration_audio` and evicted least
recently heard past `NARRATION_AUDIO_CACHE_BYTES` (128 MB by default), so a
replay costs nothing. When the hosted voice cannot be reached the browser's own
speech synthesis reads instead, and says so.

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
requests. Postgres integration tests require a database: the local Supabase
stack, or `scripts/local_postgres.sh` where Docker is unavailable. The
ingestion-pipeline and worker tests additionally need Supabase's Storage API
and so only run against the full stack.

## Docker

Start Supabase on the host first, then run the API, worker, and frontend
containers:

```bash
scripts/local.sh up-containers
```

For local Supabase, the containers reach the host database and Storage through
`DOCKER_DATABASE_URL` and `DOCKER_SUPABASE_URL`. The committed Compose defaults
are local-only. Hosted services are configured through environment-scoped
Railway variables and are never selected by the local script.

The API and worker share one image and differ only by command: the worker runs
`python -m worker.main` and publishes no port. Splitting them into a slim API
image without the parser toolchain is deferred to deployment hardening. Neither
container keeps a local database volume, and the worker's filesystem is
disposable: the source PDF and job state both live in managed storage, so
losing the container costs one attempt rather than a book.
