# API reference

FastAPI's generated OpenAPI schema is the API contract. Swagger UI provides
interactive exploration and requests; ReDoc provides a reading-oriented view
of the same schema. This fits the project because paths, Pydantic
request/response models, validation constraints, and bearer security come from
the running Python implementation. A separate manually written Swagger spec
would duplicate those definitions. FastAPI's
[automatic documentation](https://fastapi.tiangolo.com/tutorial/metadata/)
and [additional response metadata](https://fastapi.tiangolo.com/advanced/additional-responses/)
provide the documentation tools used here.

## Open the reference

Start the application with the [local setup](operations.md#local-setup), then
open the API service directly:

| Reference | Local URL | Purpose |
|---|---|---|
| Swagger UI | [localhost:8000/docs](http://localhost:8000/docs) | Filter endpoint groups, inspect schemas, authorize, and execute requests |
| ReDoc | [localhost:8000/redoc](http://localhost:8000/redoc) | Read endpoint and schema documentation |
| OpenAPI JSON | [localhost:8000/openapi.json](http://localhost:8000/openapi.json) | Import the contract into compatible clients or generate integrations |

For a hosted environment, replace `http://localhost:8000` with that
environment's **API origin**. These URLs are `/docs`, `/redoc`, and
`/openapi.json`; they are not `/api/docs`. The Next.js `/api` rewrite does not
forward `/docs`. Opening a documentation page does not start a worker or call
a model; pressing **Execute** sends a real API request.

## Authenticate in Swagger

1. Sign in through the application's Supabase login.
2. Copy your current session **access JWT**. One way is to inspect your own
   authenticated `/api/books` request in the browser's Network panel and copy
   the token from its `Authorization` header.
3. Open Swagger UI, click **Authorize**, and paste just the JWT, without
   `Bearer ` in front. Swagger supplies that prefix when sending requests.
4. Expand `GET /api/books`, click **Try it out**, then **Execute**.

The Supabase anon key, refresh token, and database password cannot substitute
for the access JWT. When the token expires, obtain the current access token
from the refreshed session and authorize again. Swagger does not perform
Supabase login or session refresh. The health and upload-limit endpoints are
public; ordinary application endpoints declare bearer security. The video
playback endpoint instead accepts a signed, expiring `token` query parameter.
See [authentication](architecture.md#authentication-and-ownership).

## Requests and response behavior

All application paths start with `/api`. JSON request bodies appear under
**Request body**, and their required fields and constraints are listed in
**Schemas**. Path, query, and header parameters appear on each operation.
FastAPI verifies ownership from the JWT; clients do not supply an `owner_id`.

| Result | Meaning |
|---|---|
| `200` / `201` | Successful read/action or created resource; check the operation's response schema |
| `202` | Work accepted; poll the relevant job endpoint for completion |
| `204` | Successful action with no response body |
| `400` | Missing or malformed headers checked by an endpoint's reservation helper |
| `401` | Missing, invalid, or expired access JWT |
| `404` | Resource unavailable to this user; this can also hide another user's resource |
| `409` | Idempotency conflict, duplicate source, or invalid operation state |
| `413` / `415` | Payload too large or unsupported media type |
| `422` | Invalid fields, source scope, or operation inputs |
| `429` | Configured quota exceeded |
| `502` / `503` | Provider, storage, database, or optional feature unavailable |

The applicable statuses depend on the endpoint. Validation errors contain
`detail` as a list of field errors; other errors commonly use a string or an
object with `code`/`message`. After an SSE response starts, failures arrive as
`error` events rather than a new HTTP status. Playback can return `206` for
byte ranges, `403` for an invalid signed link, or `416` for an invalid range.
Canonical book figures can return an empty `304` when their ETag matches.

### Grounded chat

Use a ready book ID from `GET /api/books` and set your access token in a local
shell variable named `STUDY_ACCESS_TOKEN`:

```bash
curl http://localhost:8000/api/chat \
  -H "Authorization: Bearer $STUDY_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"question":"Explain the main idea of chapter 1","book_ids":[1],"retrieval_mode":"hybrid_rerank","response_depth":"interview"}'
```

Replace `1` with an ID from your library. The response contains the grounded
`result`, server-owned conversation `state`, and `turn_index`. Send the
returned `conversation_id` on subsequent turns to continue that conversation.

For incremental output, use `POST /api/chat/stream` with the same body and
`curl -N`. Streams send `token` events with text followed by `final` with the
complete result or `error` with failure information. Heartbeat comments can
appear between events. Hierarchical summaries may buffer validation before
emitting their answer. Swagger describes the stream but a streaming client
is more useful for observing individual events.

### Uploads and durable jobs

PDF ingestion uses the following sequence:

1. `POST /api/ingestions` with an `Idempotency-Key` UUID and JSON describing
   `original_filename`, `content_type`, `content_length`, and `document_type`.
2. Upload bytes using the returned upload method: a presigned PUT or the
   Supabase resumable TUS path. The reservation is not the file upload itself.
3. `POST /api/ingestions/{job_id}/complete` verifies the upload and queues work.
4. Poll `GET /api/ingestions/{job_id}`. If outline review is required, inspect
   `toc-proposal` and submit `toc-confirmation` for the same job.

Lecture uploads first reserve with `POST /api/videos/uploads`, then PUT raw
bytes to `PUT /api/video-ingestions/{job_id}/source`. Match the reserved media
type and size. Caption/resource/audio/screenshot endpoints also accept raw
bytes with their declared `Content-Type`, rather than JSON or multipart forms.
OpenAPI documents those bodies as binary media.

Flashcard jobs are read at `/api/decks/jobs/{job_id}`; revision jobs at
`/api/revision-sheet-jobs/{job_id}`. A long-running worker must be running to
consume queued work. `POST /api/revision-sheets` returns `200` with a matching
existing `sheet`, or `202` with a `job`. Creation/retry rules are endpoint
specific: use the same idempotency key when retrying the same request after a
lost response, and a new key for a new operation. PDF, video, and course
reservation helpers require UUID keys; revision generation accepts a bounded
nonempty string. Some helpers validate required headers inside the handler
rather than in the parameter model; their missing-key failure can be `400`.
See [ingestion](ingestion.md) and [Postgres queues](architecture.md#postgres-job-queues).

## Maintain the reference

Endpoint docstrings describe behavior in Swagger/ReDoc. Feature tags group
operations. Pydantic types define ordinary JSON bodies; additional response
metadata describes alternate statuses and existing dictionary responses.
Explicit media metadata describes streams/files/raw bodies without changing
their transport or adding response validation. Shared metadata lives in
[api/documentation.py](../api/documentation.py).

When changing an endpoint, update its docstring, types, examples/media/status
metadata, and the relevant flow guide. Regenerate the catalog below:

```bash
uv run python -m scripts.export_api_reference
uv run python -m scripts.export_api_reference --check
uv run python -m unittest tests.test_api_documentation -v
```

The exporter uses `app.openapi()` without running startup hooks, connecting to
Postgres, or invoking providers. CI rejects a stale catalog. The live Swagger
schema remains the detailed source for field definitions and constraints;
this generated index is for browsing the repository.

## Endpoint catalog

<!-- BEGIN GENERATED: api-endpoints -->

157 operations, generated from the application OpenAPI schema.

### health

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/health` | Public | 200 | Check database readiness and return the running build identity. |
| GET | `/api/health/queue` | Public | 200 | Ingestion queue depth, age, and worker liveness. |

### books

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/books` | Bearer JWT | 200 | List the caller's ready books/papers. Processing items are not selectable. |
| GET | `/api/books/suggested-questions` | Bearer JWT | 200 | Get dynamic suggested questions for selected book(s) or library scope. |
| POST | `/api/books/suggested-questions/refresh` | Bearer JWT | 200 | Force re-generation of dynamic suggested questions. |
| PATCH | `/api/books/{book_id}` | Bearer JWT | 200 | Rename one book or paper. |
| GET | `/api/books/{book_id}/blocks/{block_id}/image` | Bearer JWT | 200 | Serve one canonical figure. |
| GET | `/api/books/{book_id}/chapters` | Bearer JWT | 200 | The book's chapters, in table-of-contents order. |
| GET | `/api/books/{book_id}/passage` | Bearer JWT | 200 | Canonical text of one scope, in reading-sized installments. |
| GET | `/api/books/{book_id}/source` | Bearer JWT | 200 | Sign a short-lived URL for the caller's own book PDF. |
| GET | `/api/papers` | Bearer JWT | 200 | List the caller's ready scientific papers. |

### ingestion

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/ingestions` | Bearer JWT | 200 | List the signed-in user’s recent PDF ingestion jobs and durable progress. |
| POST | `/api/ingestions` | Bearer JWT | 200, 201 | Reserve an ingestion job and its immutable upload path. |
| GET | `/api/ingestions/limits` | Public | 200 | Return active PDF upload limits: maximum bytes, pages, and allowed media types. |
| GET | `/api/ingestions/{job_id}` | Bearer JWT | 200 | Durable job status. This is what the UI polls while a job runs. |
| POST | `/api/ingestions/{job_id}/cancel` | Bearer JWT | 200 | Cancel a job. A running job stops at its next safe boundary. |
| POST | `/api/ingestions/{job_id}/complete` | Bearer JWT | 202 | Verify the uploaded object and hand the job to the worker. |
| POST | `/api/ingestions/{job_id}/retry` | Bearer JWT | 200 | Re-queue a failed job whose failure was marked retryable. |
| POST | `/api/ingestions/{job_id}/toc-confirmation` | Bearer JWT | 202 | Validate a reviewed hierarchy and re-queue the exact same source. |
| GET | `/api/ingestions/{job_id}/toc-proposal` | Bearer JWT | 200 | Return the deterministic, non-canonical hierarchy awaiting review. |

### study

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| POST | `/api/chat` | Bearer JWT | 200 | Run one grounded book/paper study turn and return the complete answer, evidence, and saved conversation state. |
| POST | `/api/chat/stream` | Bearer JWT | 200 | Stream the answer as it is generated instead of waiting for it whole. |
| GET | `/api/conversations` | Bearer JWT | 200 | List the caller's conversations, most recently used first. |
| DELETE | `/api/conversations/{conversation_id}` | Bearer JWT | 204 | Delete a saved conversation belonging to the signed-in user. |
| GET | `/api/conversations/{conversation_id}` | Bearer JWT | 200 | Full turn history, enough to render a resumed conversation intact. |
| PATCH | `/api/conversations/{conversation_id}` | Bearer JWT | 200 | Rename a saved conversation belonging to the signed-in user. |

### reading

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/conversations/{conversation_id}/side-chats` | Bearer JWT | 200 | List the side chats opened over one conversation, most recent first. |
| POST | `/api/conversations/{conversation_id}/side-chats` | Bearer JWT | 201 | Open a side chat over one or more passages of a conversation. |
| GET | `/api/reading-sessions` | Bearer JWT | 200 | Sources this reader has started, most recently read first. |
| POST | `/api/reading-sessions` | Bearer JWT | 201 | Start reading a source, or pick up where this reader left off. |
| POST | `/api/reading-sessions/{conversation_id}/anchors/resolve` | Bearer JWT | 200 | Say what a selection resolves to, before a question is asked about it. |
| PATCH | `/api/reading-sessions/{conversation_id}/position` | Bearer JWT | 200 | Record where the reader is, so the next visit opens there. |
| PATCH | `/api/side-chats/{side_chat_id}` | Bearer JWT | 200 | Rename a side chat, or replace the passages it is anchored to. |
| POST | `/api/side-chats/{side_chat_id}/turns/stream` | Bearer JWT | 200 | Stream one side-chat answer, framed exactly like a main chat turn. |

### prompts

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/prompt-settings` | Bearer JWT | 200 | Read the signed-in user’s saved answer-style profile and effective prompt. |
| PATCH | `/api/prompt-settings` | Bearer JWT | 200 | Save an answer-style profile while retaining the server’s grounding rules. |
| POST | `/api/prompt-settings/preview` | Bearer JWT | 200 | Preview the effective prompt for a proposed profile without saving it or calling a model. |

### transcription

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| POST | `/api/transcriptions` | Bearer JWT | 200 | Transcribe a dictated question so the composer can be spoken into. |

### videos

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/videos` | Bearer JWT | 200 | List the signed-in user’s lectures with publication status and ingestion progress. |
| POST | `/api/videos/uploads` | Bearer JWT | 200, 201 | Reserve a lecture upload and return the ingestion identity before sending source bytes. |
| POST | `/api/videos/youtube` | Bearer JWT | 200, 201 | Reserve an idempotent YouTube lecture ingestion job. The worker acquires and processes the source. |
| DELETE | `/api/videos/{video_id}` | Bearer JWT | 204 | Delete a lecture, then unlink the media nothing else references. |
| GET | `/api/videos/{video_id}` | Bearer JWT | 200 | Load one owned lecture, its chapters, resources, and quality-gate results. |
| PATCH | `/api/videos/{video_id}` | Bearer JWT | 200 | Update an owned lecture’s title or description. |
| PUT | `/api/videos/{video_id}/captions` | Bearer JWT | 200 | Attach a WebVTT transcript to a video, so ingestion need not buy one. |
| POST | `/api/videos/{video_id}/reingest` | Bearer JWT | 200, 202 | Rebuild this video's answers from its current set of documents. |
| GET | `/api/videos/{video_id}/resource-suggestions` | Bearer JWT | 200 | List discovered supporting-resource suggestions for an owned lecture. |
| POST | `/api/videos/{video_id}/resource-suggestions/{suggestion_id}/confirm` | Bearer JWT | 200 | Confirm a discovered resource suggestion with its role and required status. |
| POST | `/api/videos/{video_id}/resource-suggestions/{suggestion_id}/dismiss` | Bearer JWT | 200 | Dismiss a discovered supporting-resource suggestion. |
| GET | `/api/videos/{video_id}/resources` | Bearer JWT | 200 | List supporting resources attached to an owned lecture. |
| POST | `/api/videos/{video_id}/resources/links` | Bearer JWT | 201 | Attach a supporting resource URL to an owned lecture. |
| POST | `/api/videos/{video_id}/resources/uploads` | Bearer JWT | 201 | Reserve a supporting PDF upload and return the PUT URL and byte limit. |
| DELETE | `/api/videos/{video_id}/resources/{resource_id}` | Bearer JWT | 204 | Detach a supporting resource from an owned lecture. |
| PUT | `/api/videos/{video_id}/resources/{resource_id}/content` | Bearer JWT | 200 | Stream an uploaded PDF straight to storage without buffering it. |

### video-ingestion

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/video-ingestions/{job_id}` | Bearer JWT | 200 | Read an owned lecture ingestion job’s status, stage, progress, and failure information. |
| POST | `/api/video-ingestions/{job_id}/cancel` | Bearer JWT | 200 | Request cancellation of an owned lecture ingestion job at a safe worker boundary. |
| GET | `/api/video-ingestions/{job_id}/events` | Bearer JWT | 200 | Read durable ingestion events after the supplied event cursor. This returns JSON, not an SSE stream. |
| POST | `/api/video-ingestions/{job_id}/retry` | Bearer JWT | 200 | Retry an eligible failed lecture ingestion job using its durable source. |
| PUT | `/api/video-ingestions/{job_id}/source` | Bearer JWT | 200, 202 | Stream reserved upload bytes without buffering a lecture in memory. |

### video-chat

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/video-conversations` | Bearer JWT | 200 | List the signed-in user’s lecture conversations. |
| DELETE | `/api/video-conversations/{conversation_id}` | Bearer JWT | 204 | Delete an owned lecture conversation. |
| GET | `/api/video-conversations/{conversation_id}` | Bearer JWT | 200 | Load an owned lecture conversation and its saved turns. |
| PATCH | `/api/video-conversations/{conversation_id}` | Bearer JWT | 200 | Rename an owned lecture conversation. |
| GET | `/api/video-conversations/{conversation_id}/side-chats` | Bearer JWT | 200 | List the side chats opened over one lecture conversation. |
| POST | `/api/video-conversations/{conversation_id}/side-chats` | Bearer JWT | 201 | Open a side chat over one or more passages of a lecture conversation. |
| POST | `/api/video-conversations/{conversation_id}/turns` | Bearer JWT | 200 | Execute one grounded lecture turn and return the complete answer and conversation state. |
| POST | `/api/video-conversations/{conversation_id}/turns/stream` | Bearer JWT | 200 | Stream answer tokens, ending with one `final` or `error` event. |
| PATCH | `/api/video-side-chats/{side_chat_id}` | Bearer JWT | 200 | Rename a side chat, or replace the passages it is anchored to. |
| POST | `/api/video-side-chats/{side_chat_id}/turns/stream` | Bearer JWT | 200 | Stream one side-chat answer, framed like a main lecture turn. |
| GET | `/api/videos/{video_id}/conversations` | Bearer JWT | 200 | List saved conversations for one owned lecture. |
| POST | `/api/videos/{video_id}/conversations` | Bearer JWT | 201 | Start a saved conversation scoped to one owned lecture. |
| GET | `/api/videos/{video_id}/frames/{frame_id}/image` | Bearer JWT | 200 | Serve one frame preview, owner-scoped in the query itself. |
| GET | `/api/videos/{video_id}/resources/{resource_id}/content` | Bearer JWT | 200 | Serve a linked PDF so a page citation can open the page it names. |
| GET | `/api/videos/{video_id}/resources/{resource_id}/pages/{page_number}/image` | Bearer JWT | 200 | Render one page of a linked document so a cited page can be shown. |
| GET | `/api/videos/{video_id}/stream` | Signed playback token | 200, 206 | Serve the canonical video to a player, honouring range requests. |
| GET | `/api/videos/{video_id}/suggested-questions` | Bearer JWT | 200 | Get dynamic suggested questions for a video lecture. |
| POST | `/api/videos/{video_id}/suggested-questions/refresh` | Bearer JWT | 200 | Force re-generation of video suggested questions. |
| GET | `/api/videos/{video_id}/timeline` | Bearer JWT | 200 | The published version's frames, in order, for the visual timeline. |
| GET | `/api/watch-sessions` | Bearer JWT | 200 | Lectures this viewer has started, most recently watched first. |
| POST | `/api/watch-sessions` | Bearer JWT | 201 | Start watching a lecture, or pick up where this viewer left off. |
| POST | `/api/watch-sessions/{conversation_id}/anchors/resolve` | Bearer JWT | 200 | Say what a moment or stretch resolves to, before a question is asked. |
| PATCH | `/api/watch-sessions/{conversation_id}/position` | Bearer JWT | 200 | Record where the viewer is, so the next visit opens there. |

### courses

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/courses` | Bearer JWT | 200 | List the signed-in user’s courses. |
| POST | `/api/courses` | Bearer JWT | 200, 201 | Create an empty course owned by the signed-in user. |
| POST | `/api/courses/batch-youtube` | Bearer JWT | 200, 201 | Create an ordered course from a batch of YouTube lectures and queue ingestion work. |
| POST | `/api/courses/from-youtube-playlist` | Bearer JWT | 200, 201 | Create a course from a YouTube playlist and queue its selected lectures for ingestion. |
| DELETE | `/api/courses/{course_id}` | Bearer JWT | 204 | Delete an owned course. |
| GET | `/api/courses/{course_id}` | Bearer JWT | 200 | Load an owned course and its ordered lectures. |
| PATCH | `/api/courses/{course_id}` | Bearer JWT | 200 | Update an owned course’s title or description. |
| POST | `/api/courses/{course_id}/lectures` | Bearer JWT | 201 | Attach an owned lecture to a course with its position and course-specific metadata. |
| DELETE | `/api/courses/{course_id}/lectures/{video_id}` | Bearer JWT | 200 | Remove a lecture’s membership from a course and return the updated course. |
| PATCH | `/api/courses/{course_id}/lectures/{video_id}` | Bearer JWT | 200 | Update a lecture’s position or metadata within an owned course. |
| POST | `/api/courses/{course_id}/upgrade-quality` | Bearer JWT | 200 | Queue higher-quality ingestion for eligible lectures in an owned course. |

### course-chat

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| DELETE | `/api/course-conversations/{conversation_id}` | Bearer JWT | 204 | Delete an owned course conversation. |
| GET | `/api/course-conversations/{conversation_id}` | Bearer JWT | 200 | Load an owned course conversation, lecture selection, and saved turns. |
| PATCH | `/api/course-conversations/{conversation_id}` | Bearer JWT | 200 | Rename an owned course conversation or update its selected lectures. |
| POST | `/api/course-conversations/{conversation_id}/turns` | Bearer JWT | 200 | Execute one grounded turn across the conversation’s selected course lectures. |
| POST | `/api/course-conversations/{conversation_id}/turns/stream` | Bearer JWT | 200 | Stream a grounded course answer as token events, followed by a final result or error. Use a streaming HTTP client to consume events incrementally. |
| GET | `/api/courses/{course_id}/conversations` | Bearer JWT | 200 | List saved conversations for one owned course. |
| POST | `/api/courses/{course_id}/conversations` | Bearer JWT | 201 | Start a saved course conversation with an explicit lecture selection. |

### decks

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/decks` | Bearer JWT | 200 | List the signed-in user’s flashcard decks and their review/generation state. |
| POST | `/api/decks` | Bearer JWT | 202 | Queue a deck for one chapter or one lecture. |
| POST | `/api/decks/cards/{card_id}/review` | Bearer JWT | 200 | Record a review grade for an owned card and return its updated review schedule. |
| POST | `/api/decks/cards/{card_id}/side-chats` | Bearer JWT | 200 | Ask about a passage of a card, in a side chat anchored to it. |
| GET | `/api/decks/jobs/{job_id}` | Bearer JWT | 200 | Read an owned flashcard generation job’s progress and result. |
| POST | `/api/decks/jobs/{job_id}/cancel` | Bearer JWT | 204 | Request cancellation of an owned flashcard generation job; return an empty 204 response. |
| POST | `/api/decks/jobs/{job_id}/dismiss` | Bearer JWT | 204 | Dismiss a failed generation alert while retaining its diagnostic row. |
| GET | `/api/decks/preferences` | Bearer JWT | 200 | Read the signed-in user’s daily new-card and review limits. |
| PATCH | `/api/decks/preferences` | Bearer JWT | 200 | Update the signed-in user’s daily new-card and review limits. |
| GET | `/api/decks/queue` | Bearer JWT | 200 | Everything due today, plus a capped number of new cards. |
| GET | `/api/decks/sources` | Bearer JWT | 200 | List every source and whether it contributes automatic/daily cards. |
| PATCH | `/api/decks/sources` | Bearer JWT | 200 | Atomically save several source choices from the settings dialog. |
| PATCH | `/api/decks/sources/{source_kind}/{source_id}` | Bearer JWT | 200 | Enable or pause one owner-scoped source without deleting its history. |
| POST | `/api/decks/sources/{source_kind}/{source_id}/automatic-set-1` | Bearer JWT | 202 | Explicitly queue missing initial sets for one legacy source. |
| GET | `/api/decks/{deck_id}` | Bearer JWT | 200 | Load an owned deck with its cited cards and generation metadata. |
| GET | `/api/decks/{deck_id}/conversation` | Bearer JWT | 200 | The conversation that holds this deck's cards, created if absent. |
| POST | `/api/decks/{deck_id}/reset` | Bearer JWT | 204 | Send every card in one deck back to new, keeping the review log. |

### notifications

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/decks/reminder-preferences` | Bearer JWT | 200 | Read the signed-in user’s daily review reminder opt-in, local time, timezone, and next scheduled instant. |
| PATCH | `/api/decks/reminder-preferences` | Bearer JWT | 200 | Save daily review reminder preferences and schedule the next future occurrence. The worker creates notifications. |
| GET | `/api/notifications` | Bearer JWT | 200 | List non-dismissed notifications and the unread count for the signed-in user. |
| POST | `/api/notifications/read-all` | Bearer JWT | 204 | Mark all active notifications as read; return an empty 204 response. |
| POST | `/api/notifications/{notification_id}/dismiss` | Bearer JWT | 200 | Dismiss one owned notification and mark it as read. |
| POST | `/api/notifications/{notification_id}/read` | Bearer JWT | 200 | Mark one owned notification as read. Repeated acknowledgements preserve its first read time. |

### revision sheets

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/revision-sheet-jobs/{job_id}` | Bearer JWT | 200 | Read an owned revision generation job’s status, stage, result identity, and error details. |
| POST | `/api/revision-sheet-jobs/{job_id}/cancel` | Bearer JWT | 200 | Request cancellation of an owned revision generation job and return its updated public state. |
| POST | `/api/revision-sheet-jobs/{job_id}/retry` | Bearer JWT | 202 | Queue a fresh attempt for a failed or cancelled revision job. Other states are rejected; Idempotency-Key is required. |
| GET | `/api/revision-sheets` | Bearer JWT | 200 | List the latest revision sheet for each owned scope and recent generation jobs. Filter books or papers with document_type. |
| POST | `/api/revision-sheets` | Bearer JWT | 200, 202 | Reuse a matching revision sheet (200) or return a queued/existing generation job (202). Idempotency-Key is required; poll the job endpoint until ready. |
| GET | `/api/revision-sheets/{sheet_id}` | Bearer JWT | 200 | Load an owned revision sheet with its content, provenance, citation references, layout, and source/settings change flags. |
| POST | `/api/revision-sheets/{sheet_id}/ask` | Bearer JWT | 200 | Answer a question using the complete canonical source scope behind an owned revision sheet, with validated citations or an insufficient-evidence explanation. |
| GET | `/api/revision-sheets/{sheet_id}/pdf` | Bearer JWT | 200 | Download the owned revision sheet’s PDF bytes with a private cache policy and ETag. |
| POST | `/api/revision-sheets/{sheet_id}/regenerate` | Bearer JWT | 202 | Queue a fresh version of an owned revision sheet using the current source/settings. Idempotency-Key is required. |

### interviews

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/interviews` | Bearer JWT | 200 | List the signed-in user’s saved interview sessions. |
| POST | `/api/interviews` | Bearer JWT | 201 | Create an interview session for validated source scope and settings. |
| POST | `/api/interviews/preflight` | Bearer JWT | 200 | Validate interview scope and settings before creating a session. |
| GET | `/api/interviews/{session_id}` | Bearer JWT | 200 | Load an owned interview session with its current question, checkpoint, and evaluated turns. |
| POST | `/api/interviews/{session_id}/answers` | Bearer JWT | 200 | Evaluate a submitted answer against source evidence and advance the owned interview session. |
| POST | `/api/interviews/{session_id}/clarifications` | Bearer JWT | 200 | Clarify the active task without recording or evaluating an answer. |
| POST | `/api/interviews/{session_id}/coding-hints` | Bearer JWT | 200 | Provide a bounded hint for the active interview coding task. |
| POST | `/api/interviews/{session_id}/finish` | Bearer JWT | 200 | Finish an owned interview session and persist its assessed state. |
| POST | `/api/interviews/{session_id}/pause` | Bearer JWT | 200 | Pause an owned interview session while preserving its resumable checkpoint. |
| GET | `/api/interviews/{session_id}/report` | Bearer JWT | 200 | Load the assessment report for an owned interview session. |
| POST | `/api/interviews/{session_id}/resume` | Bearer JWT | 200 | Start or resume an owned interview session and prepare its active question. |
| POST | `/api/interviews/{session_id}/screen-checkpoints` | Bearer JWT | 200 | Analyze raw image bytes for the active work-sample question and save the observation. Raw screenshots are not stored. |
| POST | `/api/interviews/{session_id}/start` | Bearer JWT | 200 | Start or resume an owned interview session and prepare its active question. |
| POST | `/api/interviews/{session_id}/transcriptions` | Bearer JWT | 200 | Transcribe one ephemeral answer clip with the low-cost interview model. |
| GET | `/api/interviews/{session_id}/turns/{turn_index}/clarifications/{clarification_index}/speech` | Bearer JWT | 200 | Speak one persisted interviewer clarification. |
| GET | `/api/interviews/{session_id}/turns/{turn_index}/reaction-speech` | Bearer JWT | 200 | Speak the settled answer reaction before the next question begins. |
| GET | `/api/interviews/{session_id}/turns/{turn_index}/speech` | Bearer JWT | 200 | Synthesize audio for one question in an owned interview session. |
| POST | `/api/interviews/{session_id}/voice-connection` | Bearer JWT | 200 | Create an optional LiveKit voice connection after verifying interview ownership and feature availability. |

### ideal-interviews

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| GET | `/api/ideal-interviews` | Bearer JWT | 200 | List the signed-in user’s saved ideal interview flows. |
| POST | `/api/ideal-interviews` | Bearer JWT | 201 | Generate and save an ideal interview flow for the requested source scope. |
| GET | `/api/ideal-interviews/{flow_id}` | Bearer JWT | 200 | Load an owned ideal interview flow and its generated exchanges. |
| POST | `/api/ideal-interviews/{flow_id}/voice-connection` | Bearer JWT | 200 | Create an optional voice playback connection for an owned ideal interview flow. |

### narration

| Method | Path | Access | Success | Purpose |
|---|---|---|---|---|
| POST | `/api/narration/figures` | Bearer JWT | 200 | Return available spoken descriptions for owned figure blocks, keyed by block ID. A missing entry means no description is available. |
| POST | `/api/narration/speech` | Bearer JWT | 200 | Speak one chunk of a narration script. |
| POST | `/api/narration/voice-connections/{conversation_id}` | Bearer JWT | 200 | Mint a microphone-only room token after conversation ownership checks. |

<!-- END GENERATED: api-endpoints -->
