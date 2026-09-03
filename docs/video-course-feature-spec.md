# Video course feature

Status: implemented locally; production canary pending  
Decision date: 2026-08-29

## Outcome

A course is an ordered collection of lectures that already use the standalone
video ingestion, retrieval, playback, and evidence pipeline. The course layer
adds organization, aggregate readiness, course-level resources, and grounded
questions over more than one lecture. It does not create a second ingestion
engine or copy lecture evidence.

The first useful release lets a reader:

1. create a named course from a YouTube playlist or ordered lecture URLs;
2. see processing and readiness for every lecture in course order;
3. add, remove, reorder, and rename lecture memberships without rewriting the
   underlying video;
4. open any lecture in the existing lecture workspace;
5. ask one question across all ready lectures, or an explicit subset; and
6. follow every citation to the originating lecture and timestamp or document
   page.

## Product boundaries

- `video.videos` remains the canonical lecture record. A course membership is
  a reference to it, not a copy.
- The same lecture may belong to more than one course. Removing a membership
  never deletes the lecture or its evidence.
- A lecture with at least one course membership is shown through Courses, not
  the standalone Videos library. Removing its final membership returns it to
  Videos.
- Only ready or degraded published lecture versions participate in course
  answers. Processing and failed lectures remain visible but are reported as
  excluded from a turn.
- Playlist discovery snapshots at most 100 ordered public lecture IDs, titles,
  and durations before calling the same atomic batch creation operation.
- Course answers are grounded only in member lectures and their attached
  resources. No web fallback and no unrestricted model-generated SQL.

## Production media and cost boundary

The first production course uses a private Cloudflare R2 Standard bucket for
large media. Railway and Supabase are not upgraded merely to retain video:

- R2 stores the canonical 1080p source, captions, frames, previews, and crops
  under owner- and content-addressed keys.
- Railway stores only a disposable verified read-through cache for tools that
  need local paths.
- Supabase stores authentication, relational metadata, durable jobs,
  checkpoints, conversations, compact evidence text/locators, generated
  lexical-search columns, and citations. It does not store video or image
  bytes. Course-created ingestion jobs do not create semantic embeddings.
- Normal playback remains the original YouTube player. The R2 source is the
  retained canonical copy for rebuilding and source disappearance, not the
  default streaming origin.
- Course retrieval remains lexical first. The production course does not add
  per-frame semantic embeddings until a fixed course evaluation demonstrates
  a miss that lexical retrieval cannot address.

The hard cost objective is incremental and excludes the already-running app
services: ingest the 20-lecture, 1080p pilot course and retain its media for 12
months for no more than USD 5.00. The measured playlist contains 25.98 hours
and 15.25 GiB of 1080p source media. After all 20 lectures published, the
course added 17,063,040,727 bytes (15.89 GiB) to R2, including captions,
selected frames, previews, regions, and recoverable intermediate objects. The
measured production allocation is:

| Cost | Measured / ceiling |
|---|---:|
| OpenRouter ingestion | $1.185376 / $1.35 hard cap |
| Local acquisition and upload | $0.00 incremental |
| Twelve months of R2 storage, without assuming the free tier | about $3.07 |
| Total measured first-year incremental cost | about $4.26 |

The residential acquisition worker uploads directly to R2, so source transfer
does not incur Railway egress. Cloudflare's included R2 allowance may reduce
the storage bill, but the USD 5.00 promise does not depend on it. Existing
Railway and Supabase subscription charges are flat application costs and are
not caused by this course.

A projected breach pauses before the next paid operation. Missing captions do
not silently trigger whole-course paid transcription; that lecture pauses for
an explicit decision. Question-answering usage is separately metered and keeps
the existing USD 0.05 per-turn ceiling.

The pilot course stores `maximum_visual_frames_per_hour = 20` in its course
metadata. This bounds paid analysis to roughly one representative frame every
three minutes while keeping full transcripts and the selected source frames
available for deterministic retrieval and future rebuilds.

Storage configuration is selected through one media-store factory. Local and
test environments keep the filesystem backend; production selects the
S3-compatible backend with an endpoint, bucket, region, access key, and secret
provided only as service secrets. The bucket is private and all browser access
is authorized by the API.

## Checkpointing and resume

Resume is hierarchical and preserves completed paid work:

1. the course records one ordinary durable ingestion job per lecture;
2. every lecture stage records a dependency hash and output manifest;
3. yt-dlp keeps its job-scoped `.part` file across a scheduled retry, while an
   in-process R2 multipart upload uses the S3 client's bounded part retries;
4. visual analysis records each frame group immediately and checks its stable
   input hash before making another provider call;
5. content-addressed stored objects are verified and reused; and
6. lexical evidence is version-scoped in Postgres and becomes visible only
   when the ingestion version publishes.

Worker leases make process death and deploys self-healing. An expired lease
returns the same job and stage to the queue. Retryable provider and storage
failures resume automatically; budget exhaustion, missing captions, changed
source identity, and quality-review failures require an explicit resume.

A restart never repeats a completed lecture, provider call, or verified R2
object. If a process dies mid-upload, the completed local source is retained
and uploaded again without redownloading YouTube; free local work may be
repeated when its incomplete output cannot be verified. The prior published
lecture version remains queryable until the replacement passes readiness
gates and publishes atomically.

## Data model

The existing tables remain authoritative:

- `video.courses`: course metadata;
- `video.course_lectures`: ordered membership plus an optional title override;
- `video.course_resources`: course-level supporting material.

Course conversations use separate tables because a turn can pin several
lecture ingestion versions:

- `video.course_conversations`: course, title, selected lecture ids, prompt
  snapshot, and server-authoritative conversation state;
- `video.course_conversation_turns`: immutable question, answer, evidence,
  citations, cost, and trace snapshot;
- `video.course_turn_versions`: the published ingestion version used for each
  lecture in a completed turn.

Deleting a course deletes memberships and course conversations, but never
deletes its lecture records or shared resources.

## API

Course management:

- `POST /api/courses` creates an empty course.
- `POST /api/courses/batch-youtube` creates a course and queues an ordered set
  of YouTube lectures in one transaction.
- `POST /api/courses/from-youtube-playlist` discovers and snapshots one
  YouTube playlist before invoking the same batch operation.
- `GET /api/courses` lists owner-scoped courses with aggregate readiness.
- `GET /api/courses/{course_id}` returns course metadata and ordered lectures.
- `PATCH /api/courses/{course_id}` renames or redescribes the course.
- `DELETE /api/courses/{course_id}` removes only the aggregation layer.
- `POST /api/courses/{course_id}/lectures` attaches an existing lecture.
- `PATCH /api/courses/{course_id}/lectures/{video_id}` updates order or the
  course-local title.
- `DELETE /api/courses/{course_id}/lectures/{video_id}` detaches it.

Course conversations:

- `POST /api/courses/{course_id}/conversations` starts a conversation over all
  member lectures or a validated subset.
- `GET /api/courses/{course_id}/conversations` lists course conversations.
- `GET /api/course-conversations/{conversation_id}` loads turns.
- `POST /api/course-conversations/{conversation_id}/turns` executes a grounded
  multi-lecture turn.

All operations derive ownership from authentication. Course and lecture ids
from another owner resolve as not found rather than exposing their existence.

## Retrieval and answering

V1 follows the project's deterministic-first rule:

1. rewrite a dependent follow-up into a standalone query using the existing
   bounded conversation-history pattern;
2. run Postgres English FTS over evidence from the selected course lectures,
   restricted to each lecture's current published version;
3. retain a small globally ranked set while guaranteeing lecture diversity
   when several lectures have relevant evidence;
4. check that evidence exists and that visual questions contain visual or
   document evidence;
5. broaden the lexical query once when the first pass is insufficient;
6. synthesize one answer with `[S#]` markers; and
7. reject unsupported markers and store the exact lecture/version evidence
   snapshot.

Semantic or reranked course retrieval is added only after a course gold set
shows a lexical failure mode. The initial metrics are Recall@8 for expected
lectures, citation correctness, unsupported-claim rate, abstention accuracy,
latency, and cost.

Every evidence and citation object includes `video_id`, `video_title`, and the
course-local lecture index. Timestamp citations navigate to the existing
lecture workspace; document citations open the originating lecture resource.

## Interface

- Courses is a peer library section beside Videos.
- The course library shows lecture count, ready/processing/failed counts, and
  recent activity.
- The course page has an ordered curriculum column and a course conversation
  column. Processing lectures remain visible and explain why they are not yet
  searchable.
- The question composer defaults to every ready lecture and permits narrowing
  to selected lectures without mutating the conversation default.
- Citation labels include the lecture number and timestamp, and are keyboard
  operable with WCAG AA contrast and reduced-motion behavior.

## Acceptance criteria

1. Creating a three-lecture course queues three ordinary video ingestion jobs
   and preserves the submitted order.
2. A course appears only to its owner; its member lectures no longer appear in
   the standalone Videos library.
3. Detaching a lecture preserves it and returns it to Videos when it has no
   other course membership.
4. A course question can cite evidence from at least two lectures, with every
   marker resolving to the correct lecture and location.
5. A turn excludes non-published lectures explicitly and abstains when no
   selected lecture has sufficient evidence.
6. Re-ingesting a lecture does not rewrite old turns; new turns use the new
   published version.
7. Course deletion preserves lecture records, ingestion jobs, and evidence.
8. Repository, API, retrieval, conversation, and primary UI behavior have
   automated tests.

## Implementation sequence

1. Course repository, contracts, and CRUD/batch API.
2. Course conversation persistence and multi-lecture lexical retrieval.
3. Grounded answer generation and citation navigation.
4. Courses library and course workspace.
5. R2 media backend, managed multipart retries, and filesystem fallback.
6. Playlist URL discovery, aggregate course budget, and paid-unit resume.
7. One-lecture production canary followed by the first real course ingest.
8. Course evaluation set and retrieval comparison before semantic complexity.
