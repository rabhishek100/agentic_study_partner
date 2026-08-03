# Video production integration specification

Decision date: 2026-08-04

## Outcome and first milestone

Build the real database-backed standalone-video product in the existing
FastAPI and React application. Do not add a runtime adapter over the
experimental artifact bundle.

The first milestone ingests Stanford CME295 Lecture 1 as a standalone video
with its official PDF slides, exposes it in a separate **Videos** area of the
original UI, and supports grounded multi-turn questions over video frames,
visual transitions, transcript, and linked resources.

The database is course-ready from the first migration, but playlist ingestion
and the Courses UI are deferred until the standalone-video milestone passes
its acceptance test.

## Product boundaries

The library has three distinct product areas:

- **Books**: unchanged existing book workflow.
- **Videos**: standalone videos and their linked resources.
- **Courses**: ordered collections of video lectures with course-level and
  lecture-level resources.

A video is standalone when it has no `course_lectures` membership. A course
lecture and a standalone video use exactly the same ingestion, retrieval,
conversation, and workspace components. Courses are an aggregation layer, not
a second video engine.

The existing public book schema, ingestion packages, retrieval implementation,
and API behavior must not be migrated or generalized as part of this work.

## Source inputs and playback

Video creation accepts:

1. a YouTube URL as the preferred input; or
2. a local video upload when no YouTube source is available.

For YouTube input, ingestion retains the URL and remote identifiers as source
provenance and downloads a canonical local copy. Frame extraction, hashes,
retries, and derived-data rebuilding must not depend on continued remote
availability.

The UI uses the embedded YouTube player when a YouTube source exists, including
timestamp seeking through its player API. Uploaded videos use the HTML5 local
player. The canonical local YouTube copy remains available as an ingestion and
playback fallback.

## Supporting resources

The first version fully ingests:

- uploaded PDFs;
- PDFs supplied by URL; and
- ordinary external links as reference metadata.

PDFs reuse the proven parsing concepts from the book pipeline through the
video-specific implementation; they do not become books or book content.
PowerPoint and arbitrary web-page parsing are deferred.

Resources remain canonical independent records. Explicit link tables associate
them with videos or courses. A PDF page and a video timestamp are not treated
as aligned unless a later evaluated alignment stage establishes that relation.
Answers preserve resource identity and cite PDF pages separately from video
timestamps.

For YouTube input, the pipeline inspects the description for likely official
PDFs or course-material URLs. Discovered links are suggestions requiring user
confirmation. Explicitly supplied resources ingest immediately. Unconfirmed
suggestions do not block video ingestion.

## PostgreSQL boundary

All video-domain data lives in a dedicated PostgreSQL `video` schema. It has
separate domain, storage, ingestion, retrieval, conversation, API, and worker
packages. It may reuse only shared infrastructure such as authenticated owner
identity, database pools, model clients, logging, tracing, and physical object
storage.

Every owner-visible table carries `owner_id`, has owner-scoped indexes, and is
protected by RLS consistent with the existing application. Cross-owner IDs
must behave as not found.

The first migration defines at least these logical groups:

### Canonical source data

- `video.videos`: title, duration, readiness, source kind, playback metadata,
  current successful ingestion version, and owner.
- `video.video_sources`: YouTube identifiers/URL or upload identity, canonical
  storage object, content hash, media metadata, and acquisition provenance.
- `video.transcript_sources`: original captions or transcription artifact,
  language, provider, coverage, timing provenance, and content hash.
- `video.transcript_segments`: lossless timestamped transcript cues.
- `video.resources`: independent PDF or link identity, original bytes/storage,
  hash, media type, and provenance.
- `video.resource_pages`: lossless PDF page text and page-located canonical
  content.
- `video.video_resources` and `video.course_resources`: explicit associations.
- `video.courses` and `video.course_lectures`: minimal course metadata and
  ordered video membership.

### Durable workflow state

- `video.ingestion_jobs` and `video.ingestion_job_events`: a video-specific,
  lease-based, resumable state machine and durable progress log.
- `video.ingestion_versions`: stage configuration, dependency hashes, model and
  prompt versions, costs, quality gates, and terminal outcome.
- `video.resource_suggestions`: discovered but unconfirmed description links.

### Rebuildable derived evidence

- `video.frames`: selected full-resolution and preview objects, timestamp,
  selection reasons, perceptual/content hashes, dimensions, and OCR.
- `video.visual_observations`: model-classified visual types, concise visible
  information, technical details, importance, and model/prompt provenance.
- `video.visual_events`: before/after transitions with typed time ranges.
- `video.evidence_units`: retrieval text with modality and typed locator.
- `video.evidence_embeddings`: model, dimensions, format version, and vector.

Only frames classified by the visual model as containing a diagram, drawing,
or chart receive image embeddings. Textual slides, code, terminals, equations,
and UI screens use OCR/model-extracted text unless an explicit spatial type is
also present. The production follow-up should extract the diagram region before
embedding rather than embedding the entire frame.

### Video conversations

- `video.conversations`: one owner, one video scope, retrieval configuration,
  prompt snapshot, and rebuildable workflow checkpoint.
- `video.conversation_turns`: canonical question, grounded answer, evidence
  records, costs, and trace identifiers.

The video domain does not reuse book conversation rows. It reuses the proven
conversation mechanics and streaming contracts through video-specific types.

## Binary storage

PostgreSQL stores hashes, metadata, typed locators, and object references. It
does not store video, PDF, frame, crop, or rendered-page binaries in normal
rows. Binary objects use owner-scoped, content-addressed storage paths.

Canonical source objects are retained. Derived frames, previews, crops, and
renders are versioned and rebuildable. Deleting or superseding an ingestion
must never leave database rows pointing at missing current objects.

## Ingestion workflow

Video ingestion follows the existing PDF job experience: creation is immediate,
work continues asynchronously, progress is durable, and a restart resumes the
last compatible stage.

The video appears in the Videos library while processing. Asking questions is
disabled until readiness gates pass. The UI exposes stage-level progress:

1. acquire and validate source;
2. extract media metadata and chapters;
3. acquire YouTube captions;
4. fall back to a timestamp-capable OpenRouter audio model when captions are
   missing or fail coverage;
5. ingest confirmed PDFs and resource links;
6. select and deduplicate candidate frames;
7. run local Tesseract OCR;
8. classify and interpret before/after frame pairs through OpenRouter;
9. extract spatial regions and create diagram-only image embeddings;
10. build lexical and semantic evidence indexes;
11. compute quality gates and atomically publish the successful version.

The audio fallback model must be chosen from current OpenRouter capabilities
using a small benchmark for word accuracy, timestamp quality, latency, and
price. Model self-confidence is not a quality gate.

## Dependency-aware versioning

Every stage records a dependency hash. The latest successful version remains
queryable while a replacement is processing.

- Changed source bytes invalidate transcript, frames, observations, and all
  retrieval artifacts.
- Changed transcript configuration invalidates transcript-derived evidence,
  not frame extraction.
- Changed visual prompt/model invalidates observations, events, spatial crops,
  and dependent embeddings, not video decoding or OCR.
- Added or changed PDF resources invalidate only those resources and the
  combined retrieval layer.
- Changed embedding model invalidates embeddings, not canonical evidence text.

Failures never delete an earlier successful version. Bounded retries and one
explicit fallback apply at model boundaries; successful segment results remain
cached by content, prompt, and model hash.

## Readiness gates

Gates are modality-aware:

- retained frames cover the complete playable duration;
- at least 90% of unique selected visual states have successful structured
  observations;
- every official chapter has visual evidence when chapters exist;
- no unexplained visual-analysis gap exceeds five minutes;
- when speech is detected, transcript coverage reaches at least 95% of spoken
  duration;
- silent visual videos do not require a transcript;
- missing optional PDFs never block readiness;
- failed required modalities are shown as degraded or failed rather than
  silently omitted.

Readiness is version-specific and changes atomically only after all applicable
gates are evaluated.

## Retrieval and answering

Every question automatically searches video frames/observations/events,
timestamped transcript, and all confirmed linked resources. Each evidence item
retains its source identity and locator.

The video workflow mirrors the book workflow where its behavior is already
proven:

- server-authoritative persisted conversations;
- follow-up resolution and standalone query rewriting;
- explicit plan, retrieve, sufficiency check, bounded retry, and synthesize
  nodes in LangGraph;
- end-to-end LangSmith traces;
- token streaming and retrieval-progress events;
- insufficient-evidence and partial-answer behavior;
- prior questions and answers help resolve references but never become source
  evidence.

The answer model receives the actual top-ranked frame images or before/after
pairs in addition to extracted text. Important claims require evidence.
Conflicting video, transcript, and PDF evidence is surfaced rather than merged
silently.

The main answer shows at most four strongest visual evidence cards. An
expandable diagnostics panel shows remaining frames, transcript excerpts, PDF
pages, retrieval methods/scores, cost, and trace identity. A timestamp citation
seeks three seconds before its evidence and waits for the user to play. PDF
citations open the cited page.

## API surface for the first milestone

The video domain has a separate FastAPI router and typed contracts. Exact paths
may follow existing naming conventions, but the surface must cover:

- create a YouTube-backed video;
- initialize and complete a local-video upload;
- list and fetch owner-scoped videos;
- fetch ingestion status, stages, gates, and events;
- list, confirm, or dismiss discovered resource suggestions;
- attach/upload/list/delete video resources;
- fetch a timestamped visual timeline and evidence details;
- create/list/load/rename/delete video conversations;
- execute and stream one grounded video conversation turn.

Course tables are migrated in the first milestone, but course creation,
playlist ingestion, and Courses UI routes are deferred.

## UI milestone

The original application gains a separate Videos library and video workspace.
The book interface remains unchanged.

The workspace includes:

- YouTube or HTML5 player;
- ingestion/readiness progress;
- Ask this video conversation pane;
- clickable timestamp citations;
- inline visual evidence and before/after pairs;
- searchable visual timeline;
- independent resource panel with PDF page links;
- expandable retrieval and cost diagnostics;
- keyboard operation, WCAG AA contrast, and reduced-motion behavior matching
  the existing interface standard.

The first milestone does not add the Courses UI. The eventual course lecture
screen embeds the same video workspace inside course navigation.

## Cost policy

- Standalone-video ingestion hard cap: USD 0.50.
- Course ingestion hard cap: USD 5.00.
- Individual answer hard cap: USD 0.05.
- Actual provider-reported cost is persisted and displayed.
- A projected cap violation stops before the model request.
- Cache hits preserve original cost provenance but do not count as new spend.

## Acceptance test

The milestone is complete only when CME295 Lecture 1 is ingested from its
YouTube URL through the real `video` schema with the official slides attached
as a PDF resource, and all of the following hold:

- applicable ingestion quality gates pass;
- interruption and worker restart resume without repeating compatible paid
  work;
- the original UI lists and opens the standalone video;
- YouTube playback and timestamp citations seek correctly;
- PDF evidence opens the correct page;
- a manually reviewed set covers visual-only, multimodal, follow-up,
  multi-chapter synthesis, and unanswerable questions;
- follow-up references such as “that diagram” are rewritten correctly;
- the system admits absent evidence rather than adding general knowledge;
- cost caps and per-turn costs are verified;
- every run is inspectable in LangSmith;
- the existing book workflow and its complete test suite remain green.

## Delivery sequence and commit boundaries

1. PostgreSQL `video` schema, RLS, storage conventions, and migration tests.
2. Video/course/resource repositories and API contracts.
3. Video-specific ingestion jobs, state machine, acquisition, and transcript.
4. Frame/OCR/visual analysis stages and dependency-aware checkpoints.
5. Evidence indexes, diagram-region embeddings, and quality gates.
6. Video conversations, LangGraph retrieval/answering, streaming, and traces.
7. Videos library and workspace in the original React UI.
8. Real CME295 ingestion, manual acceptance set, measured report, and docs.

Each boundary is a separate tested commit. If a boundary becomes too large, it
is split again before implementation continues.
