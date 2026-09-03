# One ingestion-quality contract for videos and course lectures

A course is a group of videos with an order. It is not a second kind of
content, and it must not become a second, cheaper standard of ingestion —
otherwise "watch the lecture" and "study the course" answer the same question
differently, and a reader has no way to know which one they are getting.

So there is one contract, stated here, and both paths are held to it. A course
lecture references the same canonical video and the same ingestion version the
standalone-video experience uses. Course membership adds ordering and a
budget; it never adds a lower-quality representation of the same lecture.

## The twelve stages

Every video and every lecture passes through the same ordered pipeline
(`video/states.py`, `Stage`). Each stage commits a durable checkpoint keyed by
its own inputs, so an interrupted run resumes at the stage it reached rather
than repaying for the work already done.

| # | Stage | What it must leave behind |
|---|---|---|
| 1 | `acquire_source` | The canonical media object, content-addressed and verified by hash |
| 2 | `media_metadata` | Duration, container, and stream facts every later stage divides by |
| 3 | `transcript` | Timestamped cues covering the lecture, from captions or ASR |
| 4 | `resources` | Linked documents fetched and paginated, or explicitly marked unreadable |
| 5 | `frame_selection` | Frames chosen across the timeline, not clustered where change was cheap to detect |
| 6 | `ocr` | On-screen text for each selected frame |
| 7 | `visual_analysis` | What each frame shows: slide, diagram, board, code, equation |
| 8 | `spatial_regions` | Diagram and figure regions addressable on their own |
| 9 | `indexing` | The lexical index over every evidence unit |
| 10 | `embeddings` | Text and image vectors, keyed by model, revision, dimension, and format version |
| 11 | `quality_gates` | The ten measurements below, recorded with the version |
| 12 | `publish` | The version marked current, and only then visible to answers |

## The ten quality gates

Recorded against the published version and rendered into a reader's sentence
by `video/readiness.py`, ordered by how much each one narrows what the lecture
can answer:

1. `canonical_source` — the original media object still exists
2. `transcript_evidence_complete` — all transcript text reached the index
3. `visual_evidence_present` — at least some frames were captured
4. `transcript_complete` — no untranscribed stretch over the threshold
5. `visual_analysis_success` — frame analysis did not partly fail
6. `no_visual_gap_over_five_minutes` — no long stretch without a frame
7. `every_chapter_visual` — each chapter has at least one frame
8. `timeline_frames` — the opening and closing minutes are represented
9. `semantic_index_complete` — every passage is in the semantic index
10. `required_resources_ready` — every document marked required was read

A gate that fails is named with the number that failed it. "Ready (partial)"
on its own is the one thing a reader cannot act on: it says something is
missing without saying what, how much, or whether it bears on the question
they were about to ask.

## The states, and what each one promises

These are distinct on purpose. Collapsing them is how a reader ends up
trusting a lecture that cannot answer them.

| State | Meaning | ETA shown? |
|---|---|---|
| **Processing** | A worker holds the lease and is advancing a stage | Yes, from durable stage state |
| **Waiting** | Claimable, but nothing has claimed it | Only if a worker has been seen recently |
| **Retry scheduled** | A stage failed retryably; the attempt budget is not spent | Only if a worker has been seen recently |
| **Available with limitations** (`degraded`) | Published and answerable, but one or more gates failed | n/a — it is published |
| **Fully ready** (`ready`) | Published with every gate passed | n/a |
| **Failed** | The attempt budget is spent, or the failure is not retryable | No — there is nothing to count down to |
| **Blocked** | Waiting on something outside the pipeline: a cost cap, a missing source, a person | No |

Two rules follow, and both were previously broken:

- **A degraded lecture is not a ready one.** A course's ready count is
  strictly the lectures that passed every gate; degraded lectures are counted
  and shown separately. Folding them together told a reader every lecture was
  complete when some were not.
- **An ETA is a promise, so it is withheld whenever it cannot be kept** — no
  worker seen recently, a stage materially overrunning its estimate, or a job
  that has stopped for good. A countdown that cannot complete reads as the
  system working, right up until it obviously is not.

## Source versus derived

Canonical sources are immutable: the media object, its metadata, its captions,
and any linked documents. Everything else — frames, OCR, visual analysis,
regions, evidence units, indexes, embeddings, summaries — is derived, and must
be rebuildable from the canonical source without re-acquiring it.

That is what makes a rebuild safe to run and a corrupted derived table
survivable. It is also why the retention sweep must never treat a canonical
object as garbage on the word of a database that cannot account for it.

## Paid work is bought once

Every paid call is keyed by a deterministic hash of its inputs
(`embedding_input_hash` and the stage checkpoint's own key). A retry that
reaches a stage whose inputs have not changed replays the stored output rather
than repurchasing it. A rebuild carries finished work into the replacement
version for the same reason.

The same rule applies across lectures within one question: a course question
embeds its query once, not once per selected lecture.
