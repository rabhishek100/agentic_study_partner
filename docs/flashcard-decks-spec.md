# Flashcard decks

Decision date: 2026-08-08

A deck turns one chapter of a book, or one lecture, into a set of cards you can
review in a few minutes a day and still be able to explain the material aloud
in an interview.

This is not a new answering path. Generation reuses the evidence loaders,
citation contracts, and coverage machinery the summary path already has; the
new work is a card model, a deterministic coverage contract over the whole
chapter, a scheduler, and a review interface.

## What a deck is

**One deck per chapter, or per lecture.** The unit you select is the unit you
get. That keeps a generation run bounded, keeps coverage reportable against a
unit a reader recognizes, and makes regeneration cheap. Daily review draws
across every deck, so a deck being small does not make a review session small.

A deck is **derived data**. It is always rebuildable from canonical content,
never hand-edited, and it records the model, prompt version, and source
version it came from. Regenerating produces a *new version* rather than
mutating the cards you have been reviewing, so review history survives a
regeneration.

## Questions already printed in a book

A book chapter has two explicitly separate creation modes:

- **Generate from topics** writes new revision cards against the deterministic
  topic inventory described below.
- **Use questions from book** extracts exercises, review questions, study
  questions, and numbered problem directives that are present in the canonical
  PDF text. It does not turn ordinary explanatory prose into questions.

The two modes use different scope keys, produce independently versioned decks,
and appear in separate library sections. A source-authored question must never
be presented as an AI-authored question just because a model was used to parse
the PDF.

Extraction first selects explicit exercise, problem, review-question, or study
question nodes from the canonical hierarchy. Within those nodes, top-level
numbered boundaries are parsed deterministically before any answer model call.
This keeps a multi-page exercise, its setup, tables, code, and labelled
subparts in one lossless source unit and prevents narrative or lab questions
from leaking into the deck. Books without a recognizable question section use
the marker-preserving 6,000-token model fallback. A provider, schema, or
completeness failure fails the job visibly and is retryable; it is never
reported as “no questions found” or silently published as complete.

A numbered exercise is one card by default. Lettered or numbered subparts stay
with their parent when they share setup or must be solved together; a subpart
becomes its own card only when it is independently answerable and explicitly
labeled in the book. This preserves the author's problem structure without
creating oversized review cards.

Answers have two visible provenance states:

- `printed_in_book` means an answer or solution was explicitly present in the
  chapter evidence. The answer can be adjacent to the question or found later
  through bounded lexical evidence selection.
- `rag_generated` means the book printed the question but not a direct answer.
  The answer is synthesized strictly from selected chapter evidence.

Both states cite the question location and every page used for the answer.
An answer whose markers do not resolve inside the supplied evidence fails
validation. Short internal evidence aliases used during generation are
resolved back to canonical book markers before answer text is stored or shown.
The system never stores a placeholder such as “refer to the chapter,” because
that is not a useful revision card.

Topic coverage does not apply to this mode: a book may put all its exercises
in one section while testing ideas from the whole chapter. Extracted decks
instead report source questions found and completely answered. The deck is
ready only when every inventoried source question has one validated card; the
interface names any missing exercise rather than displaying a vacuous 0-of-0
100% score. A successful scan with no explicit questions produces an empty
ready deck with the notice “No questions printed in this chapter.”

## Card types

The generator chooses a type per topic, from what the material supports.

| Type | Front | Back | Use when |
|---|---|---|---|
| `qa` | An interviewer's question | A structured, cited model answer | The topic is something you would be asked to explain |
| `concept` | A term or idea | Definition, why it matters, the one-line version to say aloud | The topic is a named thing you must recall on cue |
| `mcq` | A question and four options | The correct option, plus why each distractor is wrong | The topic is a fact, definition, or an easily confused distinction |
| `system_design` | A design prompt | Components, data flow, trade-offs, failure modes, and the source figure or lecture frame when one exists | The topic is an architecture, pipeline, or protocol |

MCQ distractors must come from genuinely confusable neighbours inside the same
scope. A distractor invented from outside the evidence teaches a wrong
association, so distractor text is validated against the evidence the same way
answers are.

## Grounding

Non-negotiable principle 1 applies unchanged: **every substantive claim on a
card's back carries a citation.** Book cards cite `[N<node>:P<page>]`; lecture
cards cite `[S<rank>]` against the published evidence units, exactly as the
existing answer paths do. A card whose citations do not resolve inside the
deck's own scope is dropped before the deck is stored, and the drop is counted
in the deck's metrics rather than hidden.

Interviews ask things a chapter does not literally contain. A card may
therefore carry one optional **interview angle** — a follow-up probe or a
real-world framing — which is:

- generated separately from the grounded back,
- stored in its own field, never mixed into cited prose,
- tagged `model_knowledge` and rendered visually distinct,
- never counted toward coverage or citation metrics.

This is the same labelled-source distinction the external-QA path already
makes. The grounded core stays measurable; the interview realism sits beside
it, honestly marked.

## Coverage

"Covers all the topics in the chapter" is a property the deck has to be able to
prove, so it is a deterministic contract, not a prompt instruction.

1. **Inventory** the scope in plain Python. For a book chapter, the content
   -bearing nodes of the subtree — the same node set `study.summarize` requires
   a summary to cite. For a lecture, `video.lecture.coverage_units` — published
   chapters when the source has them, transcript windows when it does not.
   Nodes and windows too thin to carry content are marked optional, so silence
   never forces a padded card.
2. **Generate per topic.** One model call per topic (batched across small
   adjacent topics), given only that topic's evidence plus a short scope
   preamble. This bounds every call well inside the context window, which is
   what makes a 60-page chapter work at all, and it stops the model
   over-sampling the first third of a long chapter.
3. **Validate**, deterministically: citations resolve, cited pages/ranks are
   inside the topic, MCQ has exactly one correct option, no card duplicates
   another card's front.
4. **Repair** once. Any required topic left with no surviving card gets one
   targeted regeneration pass with its evidence supplied again. A second pass
   has nothing new to say — the same evidence was already sent twice.
5. **Report.** The deck stores `topics_total`, `topics_covered`,
   `cards_dropped_invalid_citation`, `cards_dropped_duplicate`, and the card
   -type histogram. The interface shows coverage as a number.

A deck that cannot reach full required coverage after repair is still stored,
marked `partial`, and names the topics it missed. Silently shipping an
incomplete deck as complete is the failure mode this whole section exists to
prevent.

## Priority

Every card carries an `interview_priority` from 1 (peripheral) to 5 (an
interviewer will almost certainly probe this), with a one-line justification.
Priority is assigned at generation time from the same evidence, and it is what
makes "top questions" the default experience without giving up coverage:

- the daily queue introduces new cards highest-priority first,
- the deck view can filter to the top N,
- coverage remains complete underneath.

**Known limitation.** Priority is assigned per generation call, and a call sees
one batch of topics rather than the whole chapter. The prompt anchors the scale
against the chapter, which measurably helps — an uncalibrated first version put
10 of 14 cards at priority 5, the calibrated one gives a real spread — but the
ranking is still not a global sort. A cheap improvement, if this proves to
matter in use, is a second deterministic pass that re-normalizes priorities
across the finished deck.

## Review

Scheduling is SM-2 with FSRS-style ease damping — deterministic Python, no
model call. Per card: `state`, `due_at`, `interval_days`, `ease`, `reps`,
`lapses`.

Four ratings, Anki's: `again` (1), `hard` (2), `good` (3), `easy` (4). `again`
sends a card to relearning and increments `lapses`. MCQ cards auto-grade the
selection and pre-fill the rating, which you can still override — the machine
knows whether you picked right, only you know whether you guessed.

The **Today queue** mixes all cards due across every deck with up to
`new_cards_per_day` new cards (default 10 — the number in the original
request), ordered due-first then priority-first, capped by
`max_reviews_per_day`. Both are per-owner preferences.

Every review appends to `deck_review_events`. The scheduling row is a derived
checkpoint over that log, stored rather than replayed for the same reason
`conversations.state_json` is.

### When you fail a card

The back reveals with its citations and, for a book card, its figure. Two
actions sit under it:

- **Open the source** — a book card deep-links to the cited PDF page in the
  existing viewer; a lecture card seeks the player to the cited timestamp.
- **Ask about this** — opens a grounded conversation seeded with the card and
  its evidence, using the existing side-chat seeding path. A card you keep
  failing becomes a real explanation instead of a reread.

## Generation lifecycle

On demand, as a durable job, using the queue shape the ingestion and video
workers already use: a `deck_jobs` row is the queue, the lease, and the
progress source of truth. The worker process polls it alongside the other two
queues.

Progress is reported per topic, so a long chapter shows movement rather than a
spinner. A failed job is retryable and leaves no half-written deck: cards are
inserted in one transaction at the end, and the deck is only marked `ready`
there. A ready source-question deck also exposes **Regenerate from book** on
its detail page so a corrected parser or answer prompt can create a new version
without waiting for a failed job.

## Evaluation

Every deck reports deterministic metrics at generation time, with no judge
involved: topic coverage, citation-resolution rate, dropped-card counts, card
-type distribution, and priority distribution. These are visible in the
interface and stored on the deck row, so a regression is observable without a
separate evaluation run.

An LLM-judged gold set for card *quality* and *interview usefulness* — reusing
the judge pattern in `evals/interview.py` — is a follow-up stage, deliberately
not in this one.

## Surfaces

```
decks/contracts.py    Card, Deck, Topic, metrics — typed boundaries
decks/topics.py       Deterministic topic inventory for book and lecture scopes
decks/prompts.py      Locked grounding + per-card-type instructions
decks/generate.py     Per-topic generation, coverage check, one repair pass
decks/extraction.py   Batched source-question extraction + grounded answers
decks/validate.py     Citation and structural validation, deck metrics
decks/scheduler.py    SM-2 scheduling and the daily queue
decks/store.py        Postgres persistence for decks, cards, review state
decks/jobs.py         Deck generation queue: claim, lease, progress, retry
decks/worker.py       The runner the worker process polls
api/decks.py          Owner-scoped HTTP surface
frontend/app/decks/   Library, Today queue, review view
```

## First measured run

Chapter 3 of *The Hundred-Page Machine Learning Book*, generated with
`openai/gpt-5.6-luna`:

| | |
|---|---:|
| Required topics | 11 |
| Topics covered | 11 |
| Cards written | 15 |
| Cards dropped (uncited or out of scope) | 0 |
| Cards dropped (duplicate or malformed) | 0 |
| Cards carrying an interview angle | 9 |
| Types | 7 qa, 5 concept, 2 system design, 1 mcq |
| Repair pass needed | no |

The MCQ's distractors came from the chapter's own neighbouring rules — the
regression averaging rule offered against the classification majority rule —
which is the behaviour the distractor constraint exists to produce.

## Out of scope

- Deck sharing, export to Anki, or import. Single-owner, in-app.
- Auto-generating easier prerequisite cards from repeated failures. Genuinely
  useful, but an unbounded generation loop; revisit with data from real review
  history.
- Cross-chapter or whole-book decks. The daily queue already spans decks,
  which is what that would have been for.
