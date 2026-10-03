# Verify the complete application

Updated 2 October 2026. Run this in order: local health → deterministic tests →
connected journeys → live product smoke → hosted observability → budgeted quality
review. Mark each check PASS, FAIL or BLOCKED, with its evidence. A missing
provider/source or skipped test is a named gap, not a pass.

This guide covers every current feature family. The 54-case quality suite goes
in depth on chat, complete summaries, video/course study, sheets and interviews;
other features have contract tests and live smoke checks, not equivalent gold-set
quality coverage. LLM-as-judge is the default reviewer. Manual ratings are optional.

## 1. Check the local environment

From the repository root:

```bash
scripts/local.sh doctor
curl --fail --silent --show-error http://localhost:8000/api/health
curl --fail --silent --show-error http://localhost:8000/api/health/queue
```

Expected: web, API, local Auth/Storage/Postgres are reachable. The queue endpoint
shows worker/queue state; it does not prove a particular queued job completed.
Open `http://localhost:3000` and sign in. Local auth mail is at
`http://127.0.0.1:54324`. API schemas and routes are at
`http://localhost:8000/docs` and in [the endpoint catalog](api.md#endpoint-catalog).

Existing credentials are already configured: do not overwrite `.env` with
`.env.example`. If services are stopped, use `scripts/local.sh up`. The local
helper manages its own app/web processes. If a service was started directly,
restart it using the same launcher rather than starting a duplicate process.
See [process commands](operations.md#processes).

To activate changed monitoring settings, restart the API, worker and enabled
voice workers when their current jobs have finished. For a helper-managed stack,
`scripts/local.sh down` then `scripts/local.sh up` reloads the environment and
preserves data. Do not use `reset` for verification. Next dev must also restart
for changed public analytics variables; a production web image needs rebuilding.

## 2. Run all deterministic backend tests

Use the prepared test database, separate from the application corpus and workers:

```bash
export TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/study_partner_eval_test
uv sync --frozen --extra voice
uv run --frozen --extra voice python -m playwright install chromium
uv run --frozen --extra voice python -m scripts.bootstrap_postgres \
  --url-env TEST_DATABASE_URL --audit-only
uv run --frozen --extra voice python -m pytest tests -q -ra
```

Expected: no failures. Inspect every skip reason in `-ra`; do not compare counts
as the acceptance condition. This exercises ingestion/parser/storage, retrieval,
chat/summaries/anchors, cards/scheduling/reminders, sheets, interviews, speech
contracts, queue recovery, ownership/auth, tracing, and evaluator/budget integrity.
The latest full application checkpoint had 2,080 passing tests before the later
review additions; the latter separately passed 61 targeted cases.

For Storage integration, the test process also needs `SUPABASE_URL` pointing to
loopback and its **local development** service-role key. Obtain that key from
local setup and set it privately in the shell; do not use a hosted project key.
Rerun with those two variables set and inspect the changed skip list. Previously,
35 Storage skips passed with local Storage; three corpus-dependent checks remained.
Global queue tests skip if their database contains live jobs. Keep the dedicated
`_test` database free of another worker. If the audit reports missing migrations,
apply them to this test database with the same bootstrap command without
`--audit-only`. Creating the database is a one-time setup step if it does not exist.

## 3. Check the complete frontend and generated contracts

Run from the repository root:

```bash
npm --prefix frontend ci
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run lint:tokens
npm --prefix frontend run build
uv run --frozen --extra voice python -m scripts.export_api_reference --check
uv run --frozen --extra voice python -m scripts.export_langgraph_diagrams --check
uv run --frozen --extra voice python -m scripts.build_eval_manifest --check
```

Expected: tests/types/token lint/build pass; endpoint catalog, five graph diagrams
and 54-case manifest are current. The preceding frontend checkpoint had 753
passing tests. These checks do not exercise real sign-in or audio devices.
CI additionally audits production dependencies and builds/parses a PDF in the
production Docker image; [the workflow](../.github/workflows/ci.yml) contains the
exact image smoke. Hosted CI triggers on main or PRs, not every branch push.

For changes to API rewrites or long synchronous generation, also run
`npm --prefix frontend run verify:proxy`. This local smoke check starts the real
Next server and delays an ideal-interview POST by 35 seconds. Expect 201 with
the body and authorization preserved; it makes no paid calls. It specifically
guards against Next's former 30-second default rewrite timeout.

To check the production API image locally, including a real digital-PDF parse:

```bash
docker build --tag study-partner-verification .
docker run --rm -i study-partner-verification python - <<'PY'
from importlib.util import find_spec
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory
import fitz
import api.main, worker.main
from ingestion.config import IngestionLimits
from ingestion.preflight import preflight
from parsing.parser import parse_book
assert which('pdftoppm') and which('tesseract')
assert find_spec('gradio') is None
with TemporaryDirectory() as temporary:
    directory = Path(temporary)
    source = directory / 'smoke.pdf'
    document = fitz.open()
    document.new_page().insert_text((72, 96), 'Monitoring input distributions detects training-serving skew.')
    document.set_toc([[1, 'Chapter 1', 1]])
    document.save(source)
    document.close()
    assert preflight(source, limits=IngestionLimits()).document_class == 'structured_digital'
    book = parse_book(source, force=True, book_cache=directory/'b.json', elements_cache=directory/'e.json')
    assert len(book.sections) == 1 and book.sections[0].full_text.strip()
print('PASS: production imports, PDF/OCR tools and digital PDF parse')
PY
```

## 4. Run connected browser checks without paid models

After the frontend production build, run these separately, with the same
`TEST_DATABASE_URL` from step 2:

```bash
uv run --frozen --extra voice python -m tests.check_five_flow_journeys
uv run --frozen --extra voice python -m tests.check_eval_review_browser
```

The first starts its own frontend/API and uses real isolated SQL persistence,
SSE and worker publication, with fixture auth/models/speech. Expected PASS lines:
chat + summary, revision, lecture, course and interview. It checks reopen,
course exclusions, pause/resume, downloaded PDF bytes, no JS errors/HTTP 5xx,
keyboard focus, reduced motion and 390px horizontal fit.

The second checks default automated scores, optional manual save/reload/export,
PDF routing, draft retention, narrow layout, keyboard and unsafe-content handling.
Both scripts clean up their own fixture processes/data. Neither establishes
real credentials, provider quality, live audio or a full accessibility audit.

## 5. Smoke every real product flow

Use your signed-in application and ready sources. For each row, record the
resource/session/job ID, result and trace IDs from browser DevTools → Network.
Quality judgment belongs to the LLM evaluator; these are usability, persistence,
source-navigation and provider checks. Real generation/ingestion/speech calls
are billed separately from the capped eval experiments.

| Feature | Steps | Expected observable behavior |
| --- | --- | --- |
| Sign-in and settings | Sign in; refresh; change a preference; sign out/in | Session and preference persist; signed-out private routes are denied |
| Digital book ingestion | Upload a small technical PDF with a TOC; follow its job to ready; open it; repeat the same upload | Valid hierarchy/content and original PDF; duplicate behavior respects idempotency rather than duplicating canonical content |
| Scanned/OCR book | Upload a small scan; inspect proposed outline; correct/approve it; resume | Same job resumes after review; parsed pages and hierarchy remain accessible |
| Paper ingestion | Upload a paper; open reader and whole-paper scope | Source is identified as a paper; full scope and page references are available |
| Lecture ingestion | Add a short supported lecture; wait for publication; play and open transcript/frame/resource evidence | Ready source has indexed evidence and usable media; acquisition/processing failures are explicit |
| Course ingestion | Add a small course/playlist; inspect members and readiness | Published lectures are usable; unavailable members are visibly distinct from indexed evidence |
| Book/paper chat | Ask an answerable exact-term question, a paraphrase and a multi-part question; click a citation; ask “explain that more”; refresh/reopen | Answer stream completes once; citations navigate to source; follow-up scope and saved history persist |
| Ambiguous/unanswerable chat | Ask an ambiguous scope question and a fact absent from the source | Clarification or explicit insufficient evidence; retained general-knowledge fallback is clearly labelled and borrows no book citations |
| Source policy/web | Test stay-in-source, selected-library widening and an explicit web request | Allowed scope is respected; widening reasons persist; web links and model knowledge are labelled separately |
| PDF reader | Open a chapter/page; move page; select a passage; ask a side question; reopen | Reading position and anchors persist; citation navigation opens the appropriate location |
| Side chats | Anchor to a page, selected passage, previous answer or card; ask a follow-up | Thread retains anchor/history; prior answers are context, not independent source evidence; stale anchors are reported |
| Complete summaries | Choose a section, full chapter and full paper; include extra instructions; reopen and follow citations | Complete scope is loaded rather than top-k sampled; route and coverage warnings are visible; no silent context truncation |
| Verbatim reading | Request stored passage; advance an installment; compare its beginning/end to parsed source | Ordered page-linked source content, with no generation call for the passage itself |
| Lecture study | Ask transcript question, visual question and a time-range/whole-lecture summary; follow timestamp/frame reference | Correct source/version; visual claims have visual evidence; timestamp navigation and history work |
| Course study | Ask across published members; exclude one lecture; ask again; reopen; ask about an unavailable member | Excluded source is absent from evidence; selection persists; missing evidence is not invented |
| Flashcard creation | Generate a chapter/paper/lecture set; also try printed-question extraction where present; reopen and follow a citation | Job completes; valid cards persist with source/coverage/provenance; supported printed-question identity is retained |
| Flashcard review | Rate a card; refresh; open Today; ask in its side chat | Review event/due time persist; limits/order are respected; side chat does not change the review schedule |
| Reminders | Enable timezone/time preference with due cards; keep worker running through the due time; refresh notifications; read/dismiss; reopen Today | One daily notification per local date; persistence and read/dismiss work; browser alert only with permission and running app |
| Revision sheets | Generate small and large chapter/paper sheets; follow job; open/download PDF; refresh; ask a follow-up | Artifact bytes and warnings persist; source-based follow-up cites original content; failed job offers explicit recovery |
| Adaptive interview | Start; answer weakly then more fully; request clarification/hint; pause; refresh/resume; submit; end/open report | Exactly one saved transition per submission; scores/feedback/report persist; scope and session recover correctly |
| Ideal interview | Generate an uncached chapter through the web UI/proxy (include a >30s case); submit an overlapping identical request; reopen; inspect exchanges/citations; start and stop two-voice playback | Both requests return the same saved dialogue; only the first makes model calls; complete cited topic coverage; no candidate grading; playback reads persisted exchanges |
| Dictation/HTTP speech | Record a short answer; edit transcript; submit; play saved question/answer | Transcription fills editable draft; only explicit submit advances state; audio failures leave usable text |
| LiveKit interview | Connect; hear question; speak; stop/flush; edit; submit; disconnect/reconnect | Same API-owned session; no unsolicited advancement; stale capture/playback stops; explicit listen resumes capture |
| Read-aloud/narration | Speak answer and source passage; pause/stop; replay; interrupt with a voice question if enabled | Spoken content follows saved text; cache is identifiable; interruption preserves reading position and anchored thread |
| Library/source lifecycle | Search/filter; open/reopen saved sessions; rename where supported; delete only a disposable test source | Lists/detail views agree; access is owner-scoped; deletion/retry states are explicit |

Maintenance paths need a disposable source/database copy too: verify rebuilding
derived retrieval data preserves canonical text and source hashes; exercise
cleanup dry runs before deletion; verify source restoration against the saved
hash; test backup/restore to a separate database. These are not implied by a
passing study demo. Relevant commands are `scripts.build_vector_index`,
`scripts.restore_book_source`, `scripts.restore_book_sources` and
`scripts/backup_database.sh`; inspect their options and storage prerequisites
before running them. Embedding rebuilds can make separately billed model calls.

For reminders, an API-only process is insufficient: the long-running worker
checks every 60 seconds and the UI polls every 30 seconds. There is no closed-app
background push/email. For voice, use matching API/frontend flags and the enabled
voice worker(s) from [voice setup](interview-voice.md#local-worker).
Ingestion/OCR outline approval here checks a source-processing feature; it is
separate from the optional human quality-review step that was replaced by Luna.

## 6. Check failure, recovery and accessibility behavior

Use these across chat, summary, video/course, sheet and interview screens:

| Trigger | Expected result |
| --- | --- |
| Stop a streamed answer | UI returns to a usable state; trace ends as cancelled rather than staying open |
| Switch source or thread during a response | Late events do not overwrite the newly selected context |
| Reload a queued job or paused session | State reloads from storage; no duplicate generation/submission |
| Network offline during request; restore connection | Explicit error/recovery; no endless spinner or silently lost accepted answer |
| Provider/schema/queue failure | Saved status/findings identify the failure; bounded retries; completed data preserved |
| Wrong-owner request from a second disposable login | Private source/session/job/media denied; no data leakage |
| Bad upload, stale source anchor or over-budget scope | Clear validation/readiness/context error; no fabricated evidence or truncated full-source output |
| Keyboard only | Reach controls; visible focus; close dialogs with focus restored; submit/cancel work |
| 390px viewport, light/dark mode, reduced motion | Reachable controls, no horizontal overflow; readable text/contrast; motion preference respected |

Tests should inject provider/lease errors in the isolated database; do not change
real provider credentials to provoke errors. Full contrast and real device/audio
acceptance remain distinct from the passing fixture checks.

## 7. Verify LangSmith for every flow

Current caveat: the latest evaluation round encountered the monthly unique-trace
quota. Check account usage/exporter errors before repeating the smoke. An absent
hosted trace is not verified by local SDK timing; record quota-blocked readback
as unavailable. Earlier successful live trees remain documented in
[production verification](production-verification.md).

First prove hosted delivery with zero paid inference:

```bash
uv run --frozen --extra voice python -m scripts.check_langsmith_tracing
```

Expected: hosted smoke link and five correctly nested/correlated synthetic spans.
Then, for **each real feature row in step 5**, open its trace in the configured
application project. Use `X-LangSmith-Trace-Id` from the HTTP response, or filter
by `flow`, conversation/session ID or `job_id`.
Clear stale name filters and open the root under **Traces** to expand its tree;
sidebar names are only shortcuts. Routine notification polls/reminder checks
belong to `<LANGSMITH_PROJECT>-operations`.

Check:

- HTTP execution encloses loading, routing/retrieval, model calls and persistence;
  an SSE trace ends after the final event, not after HTTP headers.
- LangGraph nodes appear where graphs are used; Python stages are also visible
  for ingestion, cards, ideal interviews, narration and reminders.
- Queued jobs have separate worker-attempt roots joined by the same `job_id`.
  Enqueue and execution are correlated, not one span spanning queue wait.
- Every physical model/provider call appears once for usage/cost, including
  retries and repairs. Cache hits/verbatim paths add no new generation cost.
- Success, handled failure and cancellation finish with appropriate outcomes.
  Prompts/evidence/routing are inspectable within the configured capture bounds.
- Auth headers, keys and signed query strings are absent. Missing cost receipts
  stay unknown; model prices and voice estimates are distinguished from receipts.

Repeat for API, worker and each enabled voice process. The five-flow evals use
separate `study-partner-evals-*` projects, not the application project.
See [coverage and trace organization](observability.md).

## 8. Verify Grafana metrics, logs and operational traces

Open [operations dashboard](https://petitecicada3339.grafana.net/d/study-partner-operations).
Set a recent time range and select the **real application service**, not only
historical `study-partner-api-verification` smoke data.
Keep the dashboard **Environment** set to `production` for hosted checks, or
`local` for the loopback commands below.

Generate a no-model request and an intentional unauthenticated denial:

```bash
curl --silent --show-error -D - -o /dev/null http://localhost:8000/openapi.json
curl --silent --show-error -D - -o /dev/null http://localhost:8000/api/books
```

Expected: first 200, second 401. Capture `X-Trace-Id`; neither needs an LLM.
Keep services running for at least two 60-second export intervals; rate/p95
panels need multiple samples and requests. Then perform a real study operation
and inspect all five panels:

1. Request/job throughput changes for the observed flow.
2. Completion latency has data; p95 is a coarse small-sample estimate, not an SLO.
3. Failed-attempt fraction reflects a failure; an observed healthy flow can show
   zero, while no observations can remain undefined.
4. CPU shows process CPU as percent of one core; multicore values can exceed 100%.
5. Memory shows process RSS, not host/container memory or per-eval peak memory.

In Explore → Loki, filter the correct service and `trace_id`. Expect structured
route/status/outcome records without prompts, request bodies or credentials.
In Explore → Tempo, find the same `X-Trace-Id` and confirm finished HTTP/Python
spans. Logs' `langsmith_trace_id`, when present, links to the AI investigation.
For authenticated work, confirm `user_id` equals verified auth identity in logs,
spans and LangSmith metadata; anonymous/invalid-auth/system work must omit it.
Use the [account filters](observability.md#user-identity). Confirm user IDs are
not Loki stream labels or metric labels. Emails remain outside telemetry.
Repeat a worker operation and voice operation to check their separate services.
AI tokens/cost belong to LangSmith/provider receipts, not these five panels.

If empty: check process restart, export errors, endpoint/authorization, service
filter and time range. [Monitoring setup](operational-observability.md) contains
exact labels and queries; do not create duplicate dashboards to fix delivery.

## 9. Verify PostHog across the UI

Open [usage dashboard](https://us.posthog.com/project/639444/dashboard/2157665)
and the project's event/activity view. In the running app:

1. Navigate library → reader → chat → lecture/course → cards → sheets → interview.
2. Click an action on each screen, sign in/out, then complete a streamed answer.
3. Compare emitted events and safe properties with browser network requests.

Expected events: `$pageview`, `ui_action`, `signed_in`/`signed_out`,
`api_action_started`, `api_action_response` or `api_action_failed`, and
`study_answer_completed`/`study_answer_failed` for supported streamed study flows.
Routes replace dynamic identifiers with placeholders. There should be no prompts,
answers, emails, typed values, filenames, full private URLs or auth tokens.
Use no invented tracking text containing those fields.

Check a reload does not duplicate a completed stream event. Separately verify
analytics disabled/DNT behavior; blockers/DNT can suppress collection. For an
opted-in collection check, use a browser profile that permits collection.
A queued job's HTTP 202 is acceptance, not completion: use its backend trace.
Current analytics does not assign semantic events to every slider/media update.
The opaque PostHog person ID should match backend `user_id`. Inspect the person's
activity and paths/funnels for journeys; session recordings are disabled, so
there is no playable replay to accept. A failed custom query should be examined
in the query debugger and compared with the standard event view before treating
it as an ingestion outage.
The usage/failure/funnel dashboard should reflect real events after ingestion;
retention needs real return visits and is not an instant correctness test.

## 10. Run budgeted LLM quality evaluation

Plan coverage without generation:

```bash
uv run --frozen --extra voice python -m scripts.run_evaluations --plan
```

Expected: 54 cases mapped across the five chosen flows. For a **new paid**
experiment, with the matching canonical sources and LangSmith/OpenRouter keys:

```bash
uv run --frozen --extra voice python -m scripts.run_evaluations \
  --live --output evaluation/runs/verification-20261002 --max-usd 2
```

The cap covers generation, judging and retries together. The default budgeted
book baseline is labelled BM25; production defaults to hybrid/rerank. Unbounded
reranker pricing is rejected by the guard. Do not treat this as a paid comparison
of all retrieval strategies. Normal app ingestion/voice clicks are not capped by
this experiment. A changed source/model/code/configuration needs a new run folder.

Inspect private `report.md`, `bundle.json` and `budget.json`:

- All intended cases attempted, with explicit generated/failed/blocked statuses.
- Route/scope/history/source exclusions, citation locator validity and evidence
  recall meet expected contracts, or failures are explained.
- Luna receives exact original context/images and independent criteria. PDF
  sheets include all rendered pages; generated output is not source truth.
- Grounding, correctness, concept coverage, usefulness, per-criterion omissions,
  unsupported claims and PDF findings are recorded. Unknowns are not perfect scores.
- Trace-backed latency/tokens, process CPU/RSS and reported/estimated costs are
  distinguishable. First-content timing is unknown for buffered generation.
- Shared receipts remain below the cap; unknown reservations stay reserved.
- Existing completed outputs resume without regeneration. Explicit retries are
  separate; never count generation failure as a successful quality verdict.

Exit code 0 means the runner completed, **not** that every quality check passed.
Expect known gaps until fixed: sheet omissions/sentence completion, incomplete
answers/feedback, unavailable course evidence and the old source-only LoRA gold
versus the retained labelled general-knowledge fallback policy. Preserve those
failures; version policy expectations before comparing changes.

For automated review of the committed ten saved outputs, without regeneration:

This extra review run is optional: the full native suite already judges its
outputs, and the ten-output review results are already saved. Use it when
checking changed reviewer behavior rather than paying to repeat an unchanged run.

```bash
uv run --frozen --extra voice python -m scripts.judge_saved_evaluations \
  --output evaluation/runs/verification-review-20261002 --max-usd 1
```

This uses [the ten-case selection](../evaluation/automated_review_selection.json)
across original and repaired private run directories. Those local snapshots
must exist; a clean clone has only sanitized public results. It is resumable
against matching hashes/reviewer code and shares the $1 cap across all reviews.
Manual review is not required. To view an individual native bundle's saved scores:

```bash
uv run --frozen --extra voice python -m scripts.review_evaluations \
  evaluation/runs/verification-20261002 --port 8767
```

Port 8767 avoids the existing viewer on 8766. Open the session URL printed by
that command; do not commit its token. The saved-review-only runner produces
`automated_reviews.json`, not a native `bundle.json`, so use that file/report
rather than passing its directory to the native bundle viewer.

## 11. Record acceptance and remaining work

For each feature, save this record in a private verification log:

| Item | Record |
| --- | --- |
| Identity | Date, commit, environment, flow, source/version, session/job/case ID |
| Test | Command or action, expected result, PASS / FAIL / BLOCKED |
| Evidence | Test output, persisted artifact, HTTP status, LangSmith and Grafana trace IDs |
| Analytics | Expected event and actual safe properties, or an explicit no-event reason |
| Performance | Completion time, token usage, provider receipt/estimate, process CPU/RSS scope |
| Finding | Concrete failure, next action and the same-case rerun that would verify a fix |

Automated fixture success, hosted telemetry delivery and LLM quality are three
separate results. Do not mark a feature complete because only one layer passed.
Keep private screenshots/prompts/source content out of commits; commit sanitized
results and update [the resume tracker](evaluation-progress.md) after each
verified unit. Current evidence and open findings are in
[evaluation-results.md](evaluation-results.md).
