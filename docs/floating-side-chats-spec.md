# Floating side chats specification

This document specifies **side chats**: small, movable, minimizable chat
windows that float over a main study conversation and answer questions about a
specific passage of it, several at a time.

It records decisions and their reasons, not just the resulting shape. Where a
cheaper option was rejected, the reason is stated so the tradeoff can be
defended later rather than reconstructed.

## The problem

Both chat surfaces — the book chat (`frontend/app/page.tsx`) and the video chat
(`frontend/app/videos/[videoId]/page.tsx`) — have one composer at the bottom of
one scrolling column. An intermediate question therefore costs the reader their
place: scroll down, ask, watch the answer stream and move the viewport, then
hunt back for the paragraph that prompted the question. Repeated per question
this is slow; with two or three parallel trains of thought it is not really
possible, because one composer forces them to be serialised. The main
transcript also fills with clarifications that are not the thing being studied.

A side chat is the smallest fix that addresses all three: the question is asked
*beside* the passage, in its own window, without disturbing the main thread.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Side chat identity | A persisted child conversation (`parent_conversation_id`) | Conversation state is already server-authoritative and persisted; a browser-only side thread would vanish on refresh, could not be reopened after closing, and would be invisible to evaluation. |
| Nesting | Depth 1, enforced by a database trigger | A quote inside a side answer opens a *sibling* of the same parent. A tree of side chats has no reading order and no sensible history rendering; the constraint is in SQL because that is where invariants in this schema live. |
| Anchor contents | The reader's selection only: parent turn index plus quoted text | Markers, node ids, and chunk ids are all *derivable* from the parent turn's stored `result_json`. Storing them would duplicate canonical data and let an anchor drift from the turn it points at. Source vs. derived, applied to anchors. |
| Priority of the quoted text | Pins the evidence its citation markers name, steers the query rewrite, and appears labelled in `request_context` | This is the requested "more important context" implemented as a mechanism rather than a prompt plea. Pinned chunks enter the evidence list first, so they carry the lowest `[S…]` markers. |
| Quoted text as evidence | Never | The quote is generated answer text. `LOCKED_GROUNDING_PROMPT` and the video schema both state that prior answers are not evidence; letting a quote ground a new claim would make the system cite itself. |
| Surrounding context | A budgeted ladder, dropped bottom-up, with the drops recorded | "Everything from the main chat" grows without bound, costs more on every turn, and dilutes the priority signal in a long thread. A `tiktoken` budget makes the inclusion decision inspectable, like `build_scope_context` already is for scopes. |
| Inherited scope | Books, retrieval mode, and prompt profile are copied at creation and frozen | Matches the existing rule that a changed book selection is a new conversation. A later change to the parent must not silently reground an open side chat. |
| Seeded state | The anchored turn's answer, evidence, citations, and scope become the side chat's `previous_*` | In a side chat, "the previous answer" is the answer being asked about. This makes `prior_answer_transform` ("shorten this", "rewrite that as bullets") work on the first turn for free. |
| Analyser prompt | Extended only for side turns, via an optional instruction block | The main-chat routing prompt is measured against a frozen gold set. Appending text for every turn would invalidate those numbers to serve a feature that does not need it. |
| Default depth | `quick`, overridable per window | A side question is usually a clarification, and a long answer in a small window scrolls badly. Also the cheapest default when three windows answer at once. |
| Concurrency | Client caps concurrent streams at 3 and queues the rest | Parallel answering is the point of the feature, but N simultaneous streams with no backpressure is a rate-limit and cost spike. |
| Promotion to the main thread | Not supported; copy only | Keeping clarifications out of the main transcript is the reason the feature exists. |
| Window model | Free-float, drag by header, corner resize, soft edge snap, per-thread remembered geometry, minimize to a dock | Placement next to the passage under discussion is the whole gesture; a docked column would lose it. Snapping and remembered geometry are what keep many windows usable. |
| Modality | Non-modal, keyboard-operable windows | Several windows plus the main chat must be usable at once, so a focus trap is wrong here. Move/resize from the focused header, `Escape` minimizes, focus ring visible, reduced motion respected. |
| Narrow viewports | Below 1024px, a bottom sheet with a tab strip | Dragging needs a pointer and room. Free-floating windows on a phone are worse than a sheet, not better. |
| Delivery | One reviewable commit per stage | Each stage is independently revertable. |

## Context assembly

The core of the feature, and the part that is defensible in an interview. Given
a side chat's anchors and its parent's turns, the assembler produces four
things: the pinned chunk ids, a labelled `request_context` block, the anchored
quotes handed to the analyser, and a report of what did not fit.

The ladder, highest priority first:

1. **The anchored quotes, verbatim**, labelled as passages from a previous
   answer and explicitly not citable. Never dropped: if a single quote exceeds
   the whole budget it is truncated, and the truncation is recorded.
2. **Pinned evidence.** Each `[S…]` marker inside a quote is resolved against
   the parent turn's stored `evidence`/`citations` to a `chunk_id`, and those
   chunks are loaded and placed at the front of the side turn's evidence list.
   A quote carrying no marker falls back to the parent turn's own citations,
   then to its rank-1 evidence, so selecting an uncited sentence still anchors
   to the right part of the book.
3. **The anchored turn**, question and answer, so a follow-up can refer to
   what the passage was answering.
4. **Recent main-thread turns** (up to three), compressed to question plus
   answer opening, so "how does that relate to what you said earlier?" works.

Levels 4 then 3 are dropped when the budget runs out. Every drop is recorded in
a `SideContextReport` on the `TurnResult`, so the answer inspector and the
LangSmith trace both show what the model was and was not given.

## Data model

`conversations` gains two nullable-by-default columns rather than a new table:
a side chat *is* a conversation, with the same turns, the same RLS, the same
resume path, and the same deletion semantics.

- `parent_conversation_id uuid` — null for a root conversation. Composite
  foreign key against `(id, owner_id)` so a parent always belongs to the same
  owner, `on delete cascade` so deleting a conversation takes its side chats.
- `anchors_json jsonb not null default '[]'` — the reader's selections. Empty
  for a root conversation, and validated to stay empty there: an anchor means
  nothing without a parent turn to point at.
- A trigger rejects a parent that itself has a parent (depth 1).
- A partial index on `(owner_id, parent_conversation_id, updated_at desc)`.

`list_conversations` returns roots only, with a `side_thread_count`, so the
history sidebar shows side chats nested under their parent instead of as
siblings competing with it.

## API surface

Added:

- `POST /api/conversations/{id}/side-chats` — open a side chat on a parent turn
- `GET /api/conversations/{id}/side-chats` — list a parent's side chats
- `PATCH /api/side-chats/{id}` — rename, add or remove anchors, set depth
- `POST /api/side-chats/{id}/turns/stream` — SSE turn, same `token`/`final`/
  `error` framing as `/api/chat/stream`

Reused unchanged: `GET /api/conversations/{id}` (a side chat is a conversation,
so resume already works) and `DELETE /api/conversations/{id}`.

Changed: `ConversationDetail` gains `parent_conversation_id` and `anchors`;
`ConversationSummary` gains `side_thread_count`; `TurnResult` gains an optional
`side_context` report.

## Delivery stages

Each stage is a reviewable commit that leaves the application working.

| Stage | Content | Done when |
|---|---|---|
| 1 — Server foundation | Migration, contracts, context assembler, evidence pinning, seeded state, side-chat endpoints, tests | A side chat created over a real conversation answers a question, its answer cites the pinned chunks first, and the stored turn records what context was dropped |
| 2 — Floating window layer | Window manager (geometry, z-order, minimize, dock), keyboard operation, remembered geometry, narrow-viewport sheet, wired to the book chat | Three windows can be opened, moved, resized, minimized, restored and closed with a pointer and with the keyboard alone |
| 3 — Capture | Selection popover, per-turn anchor button, paste-into-window chips, chip removal | Highlighting a sentence opens a window anchored to it; a second reference can be pasted into that window |
| 4 — Parallelism | Concurrency cap with a visible queue state, per-window stop, independent streams | Three questions asked at once all answer, with the fourth visibly waiting |
| 5 — Video parity | The same layer over the video chat, with the ingestion-version pin its turns require | A side chat on a lecture answers with timestamped evidence from the same published version as its parent |

## Non-goals

Promotion of side answers into the main thread. Side chats of side chats.
Sharing or exporting a side thread. Cross-conversation anchors (a side chat
belongs to exactly one parent). Simultaneous editing of the same side chat in
two tabs. A native mobile window manager.

## Risks

| Risk | Mitigation |
|---|---|
| Quoted text quietly becomes evidence | The quote is passed only as `request_context` under an explicit non-citable label; pinned *chunks* are the citable path, and a test asserts the quote text never enters the evidence list. |
| Marker resolution drifts from the parent turn | Markers resolve against that turn's stored `result_json`, not against a fresh retrieval, and an unresolvable marker is skipped rather than guessed. |
| Extending the analyser prompt regresses main-chat routing | The extra instruction block is only attached to side turns; a test asserts the main-chat prompt is byte-identical to before. |
| Parallel windows multiply cost | `quick` depth by default, a concurrency cap of 3, and pinned evidence capped so a side turn's evidence set stays near a normal turn's size. |
| Free-floating windows are inaccessible | Non-modal labelled windows, keyboard move/resize, `Escape` to minimize, and a narrow-viewport fallback that does not require dragging. |
| Side chats clutter conversation history | Roots only in the list, children nested under their parent, and cascade deletion so a deleted conversation leaves none behind. |
