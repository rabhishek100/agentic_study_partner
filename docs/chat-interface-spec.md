# Chat and interface specification

This document specifies the second interface pass: a professional, non-buggy
study UI with cross-book chat, figure-aware answers, readable references, and a
PDF viewer that opens to the exact page an answer cites.

It records decisions and their reasons, not just the resulting shape. Where a
cheaper option was rejected, the reason is stated so the tradeoff can be
defended later rather than reconstructed.

## Scope change against AGENTS.md

`AGENTS.md` currently scopes the interface as "a small React interface —
functional, not polished," and lists a minimal interface under *Must have*.
This work deliberately supersedes that line. The reason is that the project's
own **Portfolio output** requirement — a demo video, an example chapter summary,
an interview session — is delivered *through* the interface. A UI that looks
provisional undercuts the retrieval and evaluation work it is presenting.

What does **not** change: the interface still consumes the existing grounded
API. No retrieval, ranking, or citation logic moves into the frontend, and no
answer content is generated client-side. Every principle in AGENTS.md still
holds, in particular *ground every answer*, *deterministic first*, and
*source vs. derived data*.

`AGENTS.md` is amended in the foundation stage to reflect this.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Styling foundation | Tailwind CSS v4 + shadcn/ui | Accessible dialog/sheet/tooltip/scroll-area primitives are prerequisites for the PDF pane and reference cards; hand-rolling them is where "buggy UI" comes from. |
| Language | TypeScript | The API result shape is about to grow (figures, book identity, persisted conversations). Compile-time contract checking is the cheapest defence against UI/API drift. |
| Figure relevance | Deterministic page-and-node adjacency | Honours *deterministic first*. No model call, no re-ingest, ships against every book already in the database. |
| PDF highlighting | Text-layer matching against the stored excerpt | The parser keeps no geometry. Matching avoids changing the canonical contract and re-parsing every book. |
| Multi-book hierarchy requests | Clarify | "Summarize chapter 3" across five books is genuinely ambiguous; the `clarify` route already exists for exactly this. Guessing would violate *admit insufficient evidence* in spirit. |
| Conversation state | Server-authoritative, persisted | The client currently owns the only copy of conversation state. Persisting it enables history, survives refresh, and makes turns replayable for evaluation. |
| Book selection | Multi-select, defaulting to all ready books | Cross-book study is the requested behaviour; scoping down is the exception, not the default. |
| Retrieval mode and diagnostics | Per-answer inspector | Keeps the *inspectable agentic workflow* claim visible without putting an un-reasonable-about dropdown on the front door. |
| Prompt customization | Locked grounding layer plus editable interview instructions/templates | Makes the exact model messages inspectable and useful without letting a typo remove citations, abstention, or book-only grounding. |
| Response depth | Quick answer, Interview answer, Deep dive | Labels map to how a reader practices an interview response; explicit wording in the question overrides the UI default. |
| Prompt persistence | Owner default plus conversation snapshot | A saved default applies to new conversations, while an existing conversation remains reproducible until the reader explicitly applies a new profile. |
| Delivery | One reviewable commit per stage | Each stage is independently revertable. |

### Interview prompt studio

The main composer keeps one compact depth selector and a Prompt settings action.
The settings sheet shows five distinct things rather than presenting one
unrestricted “system prompt” textarea:

1. Required grounding and citation rules, visible and read-only.
2. Editable interview-coach instructions.
3. Editable templates for concept explanations, system-design walkthroughs,
   and complete chapter/section interview reviews.
4. An editable user-message template whose allowed placeholders are validated;
   `{question}` and `{evidence}` are mandatory.
5. A read-only compiled system/user message preview with server-inserted evidence
   represented by a placeholder.

The server prepends locked grounding requirements to every editable profile.
Prompt profiles are owner-scoped, and every conversation stores the profile it
started with. Turns record the answer archetype, resolved depth, routing reason,
and prompt-profile version in `TurnResult`, so the inspector and persisted
history remain explainable.

## Current state and the gaps this closes

Established by reading the code, not assumed:

- **Cross-book retrieval already works.** `retrieval.search.retrieve()` and
  `study.scope._book_rows()` both accept `book_id=None`, meaning every book the
  owner has, with owner-scoped SQL throughout. The blockers are contract-level:
  `ChatRequest.book_id` is a required `int > 0`, `ConversationState.book_id` is
  singular, and the UI hard-selects one book.
- **Original PDFs are retained for ready books.** `books.source_storage_bucket`
  / `source_storage_path` point at Supabase Storage, and `ingestion/cleanup.py`
  deliberately exempts ready books from retention deletion. A viewer is
  therefore possible without re-uploading anything.
- **No geometry is stored anywhere.** `parsing/models.py` keeps `page` only, on
  text, tables and images alike. Pixel-accurate highlighting is not available
  without a parser change and a full re-parse.
- **Images are stored but inert.** `image_blocks` holds base64 payloads keyed to
  a content block, which carries a page and a node — but no caption and no alt
  text. `retrieval/chunking.py` records image blocks as zero-length chunk
  sources, so they are unsearchable, and `study/context.py` strips them before
  summarization. Nothing in the system currently knows an image is relevant to
  anything.
- **References are unreadable because they are baked into the answer string.**
  `study/query.py` appends `_Retrieval: <mode>_` and a `### Sources` markdown
  list to the answer text, which the UI renders as undifferentiated markdown —
  while the structured `evidence` and `citations` arrays on `TurnResult` are
  ignored by the UI entirely.
- **No scroll anchoring.** The message list never scrolls to follow streaming
  output.

## A. Interface foundation

Tailwind CSS v4 and shadcn/ui replace the 734-line hand-written
`app/globals.css`. All five existing frontend files migrate to TypeScript.

**Design system.** One token set defined as CSS custom properties in the
Tailwind v4 `@theme` layer: surface, border, foreground, muted, primary,
destructive, plus a citation accent. Light and dark are both first-class and
driven by `prefers-color-scheme` with an explicit user override. Typography uses
one sans stack for the interface and one serif or high-legibility stack for
answer prose, since answers are long-form reading material and the current
uniform sans is part of why the interface reads as unfinished.

**App shell.** A three-region layout: a left rail (library, book selection,
conversation history, upload), a centre column (conversation), and a right pane
(PDF viewer, collapsed by default). The centre column has a fixed maximum
measure so answer prose does not run to 200 characters per line on a wide
display.

**States.** Every asynchronous surface specifies four states — loading, empty,
error, and populated. The current interface skips several (an upload failure and
an empty library are visually identical panels of muted text). Loading states
use skeletons matching final layout, not spinners, so nothing shifts on arrival.

**Accessibility, as acceptance criteria rather than aspiration:** every
interactive element reachable and operable by keyboard; a visible focus ring
that survives the Tailwind reset; the message list as an `aria-live="polite"`
region announcing turn completion rather than every streamed token; a skip link
to the composer; `prefers-reduced-motion` honoured by every transition; contrast
at WCAG AA in both themes.

**Streaming behaviour.** Scroll anchoring that follows generation but yields
permanently once the user scrolls up, with a "jump to latest" affordance. A stop
button that aborts the in-flight `AbortController` and keeps the partial answer
rather than deleting it. Retry on the last turn. Copy on any answer. The
composer textarea auto-grows to a bounded height.

**Robustness fix carried in this stage.** `sendQuestion` currently captures the
assistant message index by assigning to a closure variable *inside* a
`setMessages` updater. State updaters must be pure; this one is not, and it is
double-invoked under StrictMode. It is replaced by a stable turn id generated
before dispatch, with messages addressed by id rather than array index. The same
change fixes the current error path, which removes the assistant placeholder but
leaves the user's question stranded with no indication the turn failed.

## B. Cross-book chat

**Contract.** `ChatRequest.book_id: int` becomes `book_ids: list[int]`,
non-empty, every entry verified against the caller's ready books by the existing
`_require_ready_book` check applied per id. `ConversationState.book_id` becomes
`book_ids: list[int]`. An empty list is rejected rather than silently meaning
"all", because a silent all-books fallback is the same class of bug the current
`book_id` field has a comment warning against.

**Retrieval.** `retrieve()` gains a `book_ids: list[int] | None` filter
alongside the existing `book_id`, implemented as `book_id = any(%s)` in the
lexical and vector queries. `book_id=None` semantics are preserved for the
scripts and evaluations that rely on them.

**Evidence must carry book identity.** `EvidenceRef` gains `book_id: int` and
`book_title: str`; `CitationRef` gains `book_id: int`. Without this a reference
list spanning three books cannot say which book a page number belongs to — the
single most important thing a cross-book citation has to communicate.

Citation markers keep their existing `[N<node>:P<page>]` form. `nodes.id` is a
database-wide identity column, so a node id is already globally unique across
books and the marker stays unambiguous. No prompt or validation change.

**Hierarchy routes under ambiguity.** `study/analyze.py` routing gains one rule:
if the route is `hierarchy_summary` or `hierarchy_list`, more than one book is in
scope, and the reference resolves against more than one of them, the turn is
re-routed to `clarify` with a question naming the matching books. Resolution
against exactly one book proceeds without interruption. Retrieval QA is never
constrained this way — spanning books is the point.

**Selection UI.** A checkbox list in the left rail, defaulting to every ready
book, with a summary chip row above the composer showing the active scope.
Selection is stored on the conversation. Changing it mid-conversation warns that
prior turns were answered under a different scope rather than silently wiping
history, which is what the current book selector does.

## C. Structured references

**Backend.** `study/query.py` stops concatenating presentation into the answer.
The `### Sources` block and the `_Retrieval: <mode>_` line are removed from the
answer string. Inline `[N<node>:P<page>]` markers stay — they are the grounding
contract, and `retrieval_mode` is already a first-class field on `TurnResult`.
`tests/test_query_routing.py` asserts on the removed string and is updated.

**Frontend.** A markdown renderer plugin parses `[N<node>:P<page>]` markers,
maps each to its `CitationRef`, and replaces it with a numbered inline chip.
Chips are keyboard-focusable; hovering or focusing one previews the book, the
hierarchy path, and the page; activating one opens the PDF pane at that page.

Below each answer, a **references** section renders from `evidence`, not from
text: one card per source showing book title, hierarchy path rendered as a
breadcrumb rather than `::`-joined text, page range, retrieval method, rank, and
the stored excerpt in a collapsible region. Cards group by book when a turn spans
several. Each card opens the PDF pane.

**Answer inspector.** A collapsible "How this answer was built" panel per
assistant turn: route, history dependency, standalone query, retrieval mode,
resolved scope, evidence ranks with scores, outcome, and any warnings. This
absorbs the sidebar's current "Last turn" panel, which only ever described the
most recent turn. Retrieval mode moves to an advanced setting on the
conversation rather than a front-door dropdown.

## D. Figures and diagrams in answers

**Selection is deterministic.** After a turn resolves, figures are selected by
querying `content_blocks` joined to `image_blocks` for blocks where the node is
in the turn's cited or evidence node set *and* the page is in that node's cited
page set. No model call, no ranking, no threshold. This is defensible precisely
because it is explainable: a figure appears if and only if it sits inside cited
evidence.

**Contract.** `TurnResult` gains `figures: list[FigureRef]`:

```python
class FigureRef(ContractModel):
    book_id: int
    node_id: int
    block_id: int
    page: int
    mime_type: str
    path: str                      # node display path
    evidence_rank: int | None      # rank of the evidence that surfaced it
```

Payloads are never inlined into the result. A result carrying base64 images
would balloon every response and every persisted turn row.

**Serving.** `GET /api/books/{book_id}/blocks/{block_id}/image` returns the
decoded bytes with the stored MIME type, owner-verified, 404 for anything not
the caller's. Because canonical content is immutable, responses carry a strong
`ETag` and `Cache-Control: private, max-age=31536000, immutable`. A response
size ceiling is enforced.

**Presentation.** Figures render in a gallery beneath the answer, each labelled
with its book, section path, and page, and each clickable to open a lightbox and
to jump the PDF pane to that page. Alt text is the section path and page — the
honest description available today. Genuine alt text requires captions.

**Known limitation, recorded deliberately.** Adjacency misses a figure that sits
one page outside the cited range, and cannot distinguish a decorative image from
a substantive diagram. The upgrade path is captioning images with a vision model
at ingest and making captions searchable so figures compete in retrieval on their
own merit. Per AGENTS.md that upgrade is justified by a measured failure, so this
stage adds a small figure gold set — questions whose correct answers have a known
associated figure — and records precision and recall of adjacency selection. If
the numbers are poor, captioning is the justified next step.

## E. PDF viewer with citation targeting

**Serving.** `GET /api/books/{book_id}/source` verifies ownership and readiness,
then returns a short-lived Supabase Storage signed URL plus its expiry. The
bytes do not proxy through FastAPI. The client refreshes the URL on expiry.
Books whose source object was removed return a 404 the UI explains, rather than
presenting a broken viewer.

**Rendering.** `react-pdf` over `pdfjs-dist`, with the worker self-hosted from
`public/` rather than a CDN, pinned to a version matching the library. Lazy page
rendering, a page cache bound by count, and a virtualised page list so a
600-page book does not render eagerly.

**Layout.** A resizable split pane: conversation left, viewer right, draggable
divider with a keyboard-operable handle, position persisted. Below the tablet
breakpoint the viewer becomes a full-screen sheet.

**Targeting and highlighting.** Opening from a citation passes
`{book_id, page, excerpt}`. The viewer scrolls to the page, then locates the
excerpt within that page's text layer:

1. Normalize both the stored excerpt and the page's text items — Unicode NFKC,
   collapse whitespace, strip soft hyphens, join words broken across line ends.
2. Slide a window over the normalized page text to find the best match, accepting
   above a similarity threshold.
3. Highlight the text-layer spans covering the matched range, scroll the first
   into view, and pulse it once (suppressed under `prefers-reduced-motion`).
4. On no acceptable match, fall back to highlighting the whole page and say so
   quietly, rather than highlighting the wrong passage.

The normalization and matching are pure functions and are unit-tested directly,
including the failure path.

**Known limitation, recorded deliberately.** Matching degrades on multi-column
layouts, heavy ligature use, and hyphenation, and the stored excerpt is
truncated to 400 characters. The upgrade path is persisting block geometry at
parse time, which changes the canonical contract, bumps `parser_version`, and
requires re-parsing every book. This stage measures the match rate across the
ingested book's evidence so that cost is decided on evidence.

## F. Persisted conversations

**Data model.** One migration adding two owner-scoped tables with RLS matching
the existing policy shape:

```sql
create table public.conversations (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    title text not null,
    book_ids bigint[] not null check (cardinality(book_ids) > 0),
    retrieval_mode text not null,
    state_json jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table public.conversation_turns (
    id bigint generated by default as identity primary key,
    owner_id uuid not null,
    conversation_id uuid not null,
    turn_index integer not null check (turn_index >= 0),
    question text not null,
    answer text not null,
    result_json jsonb not null,
    created_at timestamptz not null default now(),
    unique (conversation_id, turn_index),
    foreign key (conversation_id) references public.conversations(id)
        on delete cascade
);
```

Consistent with *source vs. derived data*: `conversation_turns` is canonical, and
`conversations.state_json` is a derived resume checkpoint rebuildable by
replaying turns. It is stored rather than recomputed because rebuilding it on
every turn is wasted work, not because it is authoritative.

**State ownership moves to the server.** Today the client posts the entire
`ConversationState` back on every turn, which makes the browser the only holder
of conversation truth and lets a client submit arbitrary state. After this
change `ChatRequest` carries `conversation_id` (or null to start one) and the
server loads, executes, and persists. `ConversationState` remains the internal
contract and is still returned on the response for the inspector, but it is no
longer *accepted* as input.

**Endpoints.**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/conversations` | List: id, title, book_ids, turn count, updated_at |
| `POST` | `/api/conversations` | Create with a book selection and retrieval mode |
| `GET` | `/api/conversations/{id}` | Full turn history for resume |
| `PATCH` | `/api/conversations/{id}` | Rename, change book selection or retrieval mode |
| `DELETE` | `/api/conversations/{id}` | Delete, cascading turns |

Titles are derived from the first question, truncated, and renameable.

**UI.** A conversation list in the left rail grouped by recency, with rename and
delete, and resume restoring messages, references, figures, book selection, and
inspector data. Deletion confirms first.

## API surface summary

Changed:

- `POST /api/chat`, `POST /api/chat/stream` — `book_id: int` becomes
  `book_ids: list[int]`; `state` is replaced by `conversation_id`; the response
  gains `conversation_id`; `TurnResult` gains `figures`; `EvidenceRef` gains
  `book_id` and `book_title`; `CitationRef` gains `book_id`; the answer string
  no longer contains a `### Sources` block or a retrieval-mode line.

Added:

- `GET /api/books/{book_id}/source`
- `GET /api/books/{book_id}/blocks/{block_id}/image`
- `GET|POST /api/conversations`, `GET|PATCH|DELETE /api/conversations/{id}`

Unchanged: the ingestion router, `/api/books`, `/api/health`,
`/api/health/queue`, SSE event names and framing.

## Non-goals

Vision-model image captions (upgrade path in D, gated on measurement). Parser
geometry and pixel-accurate overlays (upgrade path in E, gated on measurement).
PDF annotation, highlighting, or note-taking by the user. Conversation sharing
or export. Real-time collaboration. A native mobile application. Moving image
payloads out of Postgres into Storage — worth doing eventually, not required
here.

## Risks

| Risk | Mitigation |
|---|---|
| Tailwind migration regresses upload or auth | Stage 0 ports both at behavioural parity with no feature change; upload flow verified against a real ingestion before the stage lands. |
| `pdfjs-dist` worker bundling under Next 16 / Turbopack | Pin the version, self-host the worker in `public/`, verify in a production build, not only in dev. |
| Text-layer matching fails on real layouts | Explicit page-level fallback; match rate measured and reported rather than assumed. |
| Base64 image payloads inflate responses | Immutable caching, strong ETags, a size ceiling, and payloads excluded from results and persisted turns. |
| Server-authoritative state breaks scripts and tests | `execute_conversation_turn` keeps its signature; persistence wraps it at the API boundary, so CLI scripts and evaluations are untouched. |
| Removing baked-in sources breaks a test | `tests/test_query_routing.py` updated in the same commit; evaluation gold sets confirmed not to assert on the string. |

## Delivery stages

Each stage is a reviewable commit that leaves the application working.

| Stage | Content | Done when |
|---|---|---|
| 0 — Foundation | Tailwind v4, shadcn/ui, TypeScript migration, design tokens, app shell, state coverage, a11y baseline, scroll anchoring, stop/retry/copy, turn-id fix, AGENTS.md amendment | Every existing feature works as before, keyboard and contrast criteria pass, production build clean |
| 1 — References and inspector | Backend stops baking sources into answers; book identity on evidence and citations; citation chips, reference cards, per-answer inspector | A multi-source answer renders chips and cards with no `### Sources` text anywhere |
| 2 — Cross-book chat | `book_ids` through request, state, retrieval and scope; clarify on ambiguous hierarchy; multi-select UI | A question answered from two books cites both, and "summarize chapter 3" across books asks which one |
| 3 — Persisted conversations | Migration with RLS, conversation endpoints, server-authoritative state, history sidebar | A conversation survives refresh and sign-out/in; isolation test extended to the new tables |
| 4 — Figures | Adjacency selection, `FigureRef`, image endpoint, gallery and lightbox, figure gold set with measured precision and recall | An answer citing a figure page shows that figure, with the measurement recorded |
| 5 — PDF viewer | Signed-URL endpoint, split pane, page targeting, text-layer highlighting with fallback, match-rate measurement | Clicking a citation opens the correct page with the passage highlighted, with the measurement recorded |

## Test plan

**Backend.** Rejection of an empty `book_ids`; a non-owned or not-ready book id
returning the same 404 as a missing one; ambiguous hierarchy references routing
to `clarify`; deterministic figure selection over a fixture book; the image
endpoint refusing another owner's block; the source endpoint refusing a
not-ready book and handling a deleted source object; conversation CRUD; and
`tests/test_multi_user_isolation.py` extended to prove both application filters
and RLS deny cross-owner access to conversations and turns.

**Frontend.** Vitest plus Testing Library, added in stage 0 — the project has no
frontend test infrastructure today. Coverage is deliberately narrow, aimed at the
pure logic where silent breakage is likely: citation-marker parsing (including
malformed markers), excerpt normalization, and text-layer match selection with
its fallback. Component tests cover the composer's submit and stop paths and the
reference card's open-in-viewer action.

**Manual verification per stage.** Each stage is exercised in the browser
against a real ingested book before it is committed, including one deliberate
failure path.
