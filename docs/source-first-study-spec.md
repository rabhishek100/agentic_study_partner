# Source-first study specification

This document specifies **source-first study**: reading a book or paper, or
watching a lecture, as the primary activity, with grounded conversation
attached to the page or the moment rather than the other way round.

Two surfaces, one model: `/read/[bookId]` for books and papers, `/watch/[videoId]`
for lectures. Where they differ, the difference is named.

It records decisions and their reasons, not just the resulting shape. Where a
cheaper option was rejected, the reason is stated so the tradeoff can be
defended later rather than reconstructed.

## The problem

Every study surface in the application today starts with a question. The book
chat, the paper chat and the lecture chat all put a composer under a scrolling
transcript, and the source appears only as evidence *behind* an answer — a
cited page opened in the reading pane, a timestamp seeked in the player.

That serves one way of learning: you already know what you want to know. It
serves the other way badly. A reader working *through* a chapter, or watching a
lecture end to end, has questions that arise from the page in front of them —
and to ask one they must leave the page, describe in words the thing they are
already looking at, and then find their place again. The description is the
tax: "what does he mean by the box on the left of this diagram" is not a
question the retrieval system can be asked, because the retrieval system does
not know which diagram.

Worse, nothing accumulates. Forty minutes of ask-first study produces a
transcript. Forty minutes of reading should produce *a marked-up source* — the
questions in the margin next to the passages that provoked them.

## What inverts

| | Ask-first (existing) | Source-first (this document) |
|---|---|---|
| What holds the reader's place | scroll position in a transcript | page or timestamp in the source |
| What supplies context | the last few turns | **where the reader is looking** |
| What scope means | the selected books | this source, this page, this moment |
| What a session accumulates | a thread | a source with margin marks |
| Where the composer is | under the transcript | attached to the anchor |

The inversion is structural, not cosmetic. A chat column beside a document
would keep the transcript as the thing that scrolls and the document as the
reference; this specification makes the document the thing that scrolls and
conversation the thing that hangs off it.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Session identity | A reading session **is a root conversation** whose anchored questions are its side chats | Side chats are already persisted child conversations with depth-1 enforced in SQL, frozen inherited scope, cascade deletion, nested history rendering and a shared window layer. A source-first session needs exactly those properties; a second mechanism would duplicate all of them and drift. |
| Anchor citability | An answer quote is never evidence; a **resolved** source passage is | The existing rule exists because a prior answer is generated text and citing it would make the system cite itself. A reader's selection in a PDF is canonical book text, so the reason does not apply — but the *client's string* is still untrusted, so only the server-resolved canonical chunk enters the evidence list. |
| Unresolvable selections | Passed as context, never as evidence, and said so on the turn | Guessing which chunk a fuzzy selection meant would put an unverified span behind a citation marker. Source vs. derived, applied to selections. |
| Escalation | Automatic and labelled, driven by the LangGraph retry step | Chosen by the product owner over abstain-and-offer. The safety therefore moves out of the default and into the mechanism: the rung is recorded on the turn, rendered as a badge, and grounded and ungrounded content can never appear in one answer body. |
| Escalation to live web search | Only on the existing recency signal, never as a blind third retry | `execute_external_qa` already searches the web only when the query asks for something current. Making every uncovered question also spend a web search would multiply cost for questions general knowledge answers fine. |
| Library widening | Rung 2, reached by a sufficiency-gated retry, not by one widened first pass | "Open source ranked first, then library" as a single boosted query makes the boost a tuning parameter nobody can defend. As a retry it is a recorded decision with a before and after, and it costs nothing on the majority of turns the open source answers. |
| Staying in the source | A per-session **Stay in this source** lock, off by default | Automatic escalation is right for study but wrong for verification. One control, one meaning: with it on, an uncovered question abstains. |
| Ambient context | The current page or moment is always in context, shown as a removable chip | Without it the reader must select something before every question, which reinstates the tax the mode exists to remove. Removable because "ignore the page, answer generally" must not require leaving the mode. |
| Lecture ambient window | Lookback-biased: roughly the preceding 90 seconds and the following 30 | A question asked at 12:04 is nearly always about what was just said. A symmetric window spends half its budget on content the reader has not heard. |
| Book ambient scope | The current page's canonical text plus its section title path | The page alone loses "where in the argument am I"; the whole chapter is a chapter summary's worth of tokens on every turn. The hierarchy already stores the path. |
| Margin marks | Every anchor renders as a mark on the page edge or the scrubber | The accumulated artifact is the differentiator, and anchors are already persisted, so the mark is a rendering of existing state rather than a new object. |
| Chat presence | Floating windows for anchored questions, plus a collapsible session rail listing them | Windows are the marginal-note gesture and already exist. The rail exists because a window closed thirty minutes ago is otherwise findable only from conversation history, off the surface. |
| Routing | New `/read/[bookId]` and `/watch/[videoId]` routes | The layout inversion is real, and the two existing pages are already 553 and 691 lines. Separate routes are also linkable, so a citation can hand off into the reader at the page it cites. |
| Reading position | Stored on the session conversation row | A new table for one integer per source is not worth a migration's blast radius, and the video schema boundary forbids one shared table across both domains anyway. |
| Depth default | `quick` in anchored windows | Unchanged from side chats, for the same reasons: a clarification, in a small window, possibly three at once. |
| Delivery | One reviewable commit per stage | Each stage is independently revertable. |

## The ladder is the graph's retry action space

The requested "answer from the book, else the library, else the model, else the
web, by closest match" is implemented as the retry step of the existing
LangGraph loop rather than as a chain of prompt instructions. Plan → retrieve →
check sufficiency → **retry one rung wider** → answer.

| Rung | Evidence | Reached by |
|---|---|---|
| 0 | The anchor's resolved canonical units | Always. Pinned ahead of retrieval, so they carry the lowest `[S…]` markers. |
| 1 | The open source: its chapter subtree, or its transcript, frames and linked documents | The first retrieval pass. |
| 2 | The rest of the reader's ready library | A retry, when rung 1 is judged insufficient. Also reached directly by `@`-mentioning a book, which the chat already supports. |
| 3 | Model knowledge | A retry, when rung 2 is insufficient. Existing `execute_external_qa`. |
| 4 | Live web search | Only when the question carries a recency signal. Existing behaviour. |

Three invariants make this defensible rather than mushy:

1. **The rung that answered is recorded on the turn**, rendered as a badge, and
   visible in the answer inspector and the LangSmith trace. "Closest match" is
   an observable property of a run, not a claim about a prompt.
2. **Rungs 0–2 and rungs 3–4 never appear in one answer body.** An answer rests
   on the reader's sources or it is labelled as outside them. Mixing cited and
   uncited claims in one paragraph is precisely how a study tool becomes
   untrustworthy, and it is the specific risk that automatic escalation
   introduces.
3. **Widening is a retry, not a wider first pass.** Every escalation therefore
   has a recorded reason: the sufficiency verdict that caused it.

This also happens to be the strongest interview story in the feature. The
agentic workflow is not decorating a retrieval call — the routing decision is
the product behaviour the reader can see.

### What building it found

Three corrections to the assumptions above, recorded because they changed the
work rather than because they were anticipated.

**The graph had no retry step.** `AGENTS.md` describes the workflow as
plan → retrieve → check sufficiency → retry, and the compiled graph was
plan → route → execute → record. The sufficiency check and the cycle back into
retrieval are new in stage 1b, and the claim in `AGENTS.md` only became true
with them.

**Escalation already existed, in the wrong place.** `study/query.py` fell
through to external QA twice — once when retrieval returned nothing, once when
the model reported insufficient evidence — jumping straight from the open book
to model knowledge, skipping the library, and recording the jump only as prose
in `routing_reason`. The ladder does not add escalation so much as lift it out
of the query layer into somewhere it can be gated, staged and recorded.
`allow_external_fallback` already existed as a parameter and is how the graph
takes ownership: under a policy it is always false, so escalation happens in
exactly one place.

**The verdict needed no new model call.** Grounded answering already abstains
when its evidence does not support the question, and that behaviour is measured
against the frozen gold set. Reusing the abstention as the retry signal means
the ladder adds no judgement of its own — `study/grounding.py` is
deterministic throughout, climbing is monotone, and each rung is tried at most
once.

## Anchors

Three gestures, deliberately different in cost and precision.

### Ambient — "where I am"

No gesture at all. The composer carries a removable chip: `p. 142 · §4.3
Replication`, or `12:04 · slide 9`. A question typed with no selection lands on
what the reader is looking at.

### Selection — "this"

Highlighted text in the document or the transcript. A popover offers Ask,
Explain, Define and Add to deck; Ask opens a floating window anchored to the
selection. Region selection on a video frame — "what is this box in the
diagram" — is deferred, but the video pipeline already embeds frame regions, so
the ladder below accommodates it without a contract change.

### Stretch — "this bit"

A section of a book, or a marked span of a lecture. Included for lectures in
particular because the frozen video evaluation set already treats a stretch of
lecture as its judgment unit, so the gesture and the measurement agree.

### Resolution rules

| Anchor | Resolves to | Failure |
|---|---|---|
| Book page (ambient) | The canonical blocks on that page, plus the section path | A page with no parsed text is context-free; the chip says so |
| Book selection | The chunk(s) on that page whose canonical text contains the normalized selection | No match: passed as context, labelled uncitable, recorded on the turn |
| Book section | The existing resolved scope for that node | Unchanged from chapter summarization |
| Lecture moment | Transcript units in the window, plus the frame at that timestamp | A moment before the first unit resolves to the first unit |
| Lecture stretch | Every transcript and frame unit in the span, budget permitting | Over budget: truncated from the end, and the truncation recorded |

Normalization for book selections reuses `lib/pdf-match.ts`'s treatment of
ligatures, hyphenation and span splits rather than growing a second normalizer;
the matching runs in the opposite direction to the citation highlighter, over
the same text.

## Context assembly

`study.side_context` already assembles a budgeted, priority-ordered context and
reports what did not fit. Source-first turns reuse it with a different ladder,
not a second implementation. Highest priority first:

1. **The resolved anchor units**, as pinned evidence. Citable. Never dropped.
2. **The anchor as the reader framed it** — the selected text verbatim, or the
   page and section, or the timestamp — so the model knows what "this" means.
3. **The ambient window** around the anchor, when the anchor is narrower than
   the page or the moment (a selected sentence still needs its paragraph).
4. **Recent questions in this session**, compressed to question plus answer
   opening, so "and how does that relate to the last one?" resolves.

Levels 4 then 3 drop first, and every drop is recorded in the same
`SideContextReport` the answer inspector already renders.

An unresolved book selection enters at level 2 only, under a label stating it
could not be matched to the book's text — the one place where a source-first
anchor behaves like an answer quote.

## Data model

`conversations` gains three nullable columns; a source-first session is a
conversation, so it inherits turns, RLS, resume and deletion unchanged.

- `session_kind text` — null for an ask-first conversation, `'read'` or
  `'watch'` for a source-first session. A check constraint ties `'read'` to
  exactly one book id.
- `source_position jsonb` — the reader's last page or timestamp, written on
  navigation, read by the continue band. Derived, disposable, never a source of
  truth about anything else.
- `ingestion_version` on read sessions, matching what lecture turns already pin,
  so a re-parsed book's changed pagination invalidates its anchors honestly
  instead of pointing them at the wrong page.

`anchors_json` becomes a discriminated union. Existing rows are
`answer_quote` by default, so no backfill rewrites stored anchors:

```json
{"kind": "answer_quote",      "turn_index": 3, "quoted_text": "…"}
{"kind": "document_passage",  "book_id": 7, "page": 142, "selected_text": "…"}
{"kind": "document_page",     "book_id": 7, "page": 142}
{"kind": "document_section",  "book_id": 7, "node_id": 918}
{"kind": "lecture_moment",    "video_id": "…", "timestamp_ms": 724000}
{"kind": "lecture_stretch",   "video_id": "…", "start_ms": 724000, "end_ms": 1012000}
```

The depth-1 trigger, the owner composite foreign key and the partial index are
unchanged: a source-first session is a parent, its anchored questions are its
children, and neither may nest further.

## API surface

Mostly reuse. Added:

- `POST /api/reading-sessions` — open or resume the session for a source,
  returning the existing one when it exists rather than accumulating a session
  per visit.
- `PATCH /api/reading-sessions/{id}/position` — record page or timestamp.
  Fire-and-forget, rate-limited client-side.
- `POST /api/reading-sessions/{id}/anchors/resolve` — resolve a selection to
  canonical units without asking anything, so the popover can say what it found
  before a question is typed.

Reused unchanged: side-chat creation, update, streaming, detail and deletion on
both surfaces; the conversation list, which already nests children under
parents; the PDF source endpoint; the lecture playback and timeline endpoints.

Changed: `TurnResult` and `VideoTurnResult` gain the recorded grounding rung and
the reason for each widening; `ConversationSummary` gains `session_kind` so the
library can show "continue reading" against a source.

## Interface

### Read

The document holds the width. The library rail collapses to a strip, the
section navigation becomes the document's own outline, and the conversation
column is gone — replaced by margin marks down the page edge and floating
windows over the page. The composer is a single bar under the document carrying
the ambient chip; it opens a window rather than appending to a transcript.

### Watch

The player holds the width, with the transcript beside it as a scrolling,
selectable column and the linked document behind a tab, as today. Margin marks
appear on the scrubber. Pausing is not required to ask — the chip follows the
playhead and freezes when a window opens, so the answer is about the moment the
question was asked, not the moment it finished streaming.

### Both

- The session rail collapses to a badge and lists every question asked, with
  its page or timestamp, newest first. Clicking one reopens its window.
- Answers carry a rung badge. Rungs 3 and 4 render in a visually distinct
  block, above the answer body, stating plainly that this did not come from the
  source.
- **Stay in this source** is a single toggle in the session menu.
- Below 1024px both surfaces keep the source full-bleed and move conversation
  into the existing bottom sheet with its tab strip. Dragging windows on a
  phone was already rejected once; nothing here changes that.
- Keyboard: the existing arrow-key page navigation extends to the reader,
  `Escape` minimizes a window, and every anchor gesture has a keyboard path —
  selection popovers open from the keyboard selection, not only from a pointer.

## Evaluation

The gold set gains a source-first slice, because AGENTS.md forbids adding this
much machinery on the strength of it seeming useful. Each case is an anchor
plus a question, and measures:

- **Anchor recall** — do the resolved units appear in the turn's evidence?
- **Rung correctness** — for questions written to be answerable from the open
  source, from the library, and from neither, does the recorded rung match?
- **No-blend** — does any answer body mix cited and uncited claims? This is a
  hard assertion, not a metric.
- **Selection resolution rate** — what fraction of realistic PDF selections
  match canonical text, and what happens to the rest?
- **Escalation cost** — added tokens and latency per widening, from traces.

The lecture cases reuse the existing stretch-of-lecture judgment unit.

## Delivery stages

Each stage is a reviewable commit that leaves the application working.

| Stage | Content | Done when |
|---|---|---|
| 1a — Document anchors | Anchor union, resolution for pages, selections and sections, pinning, context assembly, API acceptance, tests | A side chat created with a page anchor answers from that page and cites it first; an unmatched selection is recorded as unresolved rather than guessed |
| 1b — The ladder | Grounding rung and widenings on the turn, sufficiency node and retry cycle in the graph, the stay-in-source lock, tests | A question the open book does not cover records a widening with its reason, and the same turn without a policy takes exactly the path it took before |
| 1c — Lecture anchors | Moment and stretch resolution against transcript units and frames, the same rungs on the lecture turn contract | A side chat anchored to a stretch of lecture answers from it and cites its timestamps |
| 2 — Read shell | `/read/[bookId]`, document-first layout, ambient chip, composer, floating window layer wired in, position recorded | A reader opens a book, types a question with no selection, and gets an answer grounded in the page they are on |
| 3 — Capture and marks | Selection popover, resolve-before-asking, persisted marks in the margin, reopening from a mark, multi-anchor windows | Highlighting a paragraph opens a window anchored to it, and the mark is still there on the next visit |
| 4 — Watch | `/watch/[videoId]`, playhead reporting, moment and stretch anchors, transcript selection, frame context, scrubber marks | A question asked at 12:04 answers from that stretch of lecture and cites its timestamps |
| 5 — Ladder made visible | Rung badges, the separated out-of-source block, the stay-in-source lock, widening reasons in the inspector | An escalated answer is unmistakable at a glance, and the inspector shows why it escalated |
| 6 — Continuity | Continue-reading band in the library, session rail, session recap handing off to decks and interview questions | The library offers to resume a half-read book at its page, and a finished session can become a deck |
| 7 — Evaluation | The source-first gold slice and its report | Anchor recall, rung correctness and the no-blend assertion are measured and reported |

## Non-goals

Promoting a source-first answer into an ask-first thread. Anchors spanning two
sources. Collaborative or shared highlights. Editing or annotating the source
itself — marks belong to the session, never to canonical content. Region
selection on video frames, and PDF–slide alignment, both of which the video
specification already defers. A native mobile window manager.

## Risks

| Risk | Mitigation |
|---|---|
| Automatic escalation quietly replaces the book | The rung is recorded, badged, and structurally separated in the answer body; a hard test asserts no answer mixes cited and uncited claims; the stay-in-source lock is one toggle away. |
| The playhead is unreadable | `video-player.tsx` sends postMessage commands but never listens, so the current time is not available today. Either the `listening` handshake and `infoDelivery` events, or the IFrame Player API the component deliberately avoided. Named as the one genuinely unknown piece of engineering here, and scheduled first inside stage 4. |
| Selections do not match canonical text | Resolution failure is a designed state, not an error: context-only, labelled, recorded, and measured as a rate in the evaluation slice rather than assumed to be rare. |
| Anchors drift after a re-ingest | Read sessions pin an ingestion version, as lecture turns already do; a superseded anchor says so rather than resolving against new pagination. |
| Ambient context on every turn inflates cost | The same budgeted ladder and drop report as side chats, `quick` depth by default, and the ambient window bounded by page or by seconds rather than by section. |
| Two surfaces drift apart | The window layer, the rail, the anchor editor and the queue stay in the shared descriptor in `lib/side-chat.ts`, extended with what each surface accepts, exactly as the depth control already is. |
| Scope creep eats the month | Stages 1–4 are the feature; 5 makes it honest; 6 and 7 are separately justifiable. Region selection, alignment and sharing are non-goals, in writing, above. |
