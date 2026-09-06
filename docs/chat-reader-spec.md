# Reading a chapter in the chat

A reader can ask, in the chat, for a chapter or a paper to be *reproduced*
rather than summarized: "read chapter 3 in full", "give me the chat version of
this paper", "show me the full text of Storage and Retrieval". The complete
canonical text comes back into the conversation, in order, in reading-sized
installments, with headings, tables, and figures in the places they occupy in
the source.

The motivation is readability on a phone. The library's PDFs are fixed-layout
two-column scans; on a 390-point screen a reader is either pinch-zooming or
reading four words a line. The canonical content model already holds the same
material as ordered, reflowable blocks — the parser produced them and the
summarizer consumes them — so the text a phone can actually read is already in
Postgres. This feature stops throwing it away.

## What this is not

It is not a summary, a rewrite, a simplification, or an "explain like I'm
five". No model is called on this path at all. That is the point: everything
else the chat does hands the reader *derived* text, and there was no way to ask
it for the source.

It is not the read-aloud feature. "Read chapter 3 aloud" is narration and is
deliberately excluded from this grammar; "read chapter 3 in full" is this.

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Generation | None — no LLM call on this route | AGENTS.md principle 2, *deterministic first*. Reproducing stored text needs no decision, no retry, and no decomposition. It is also what makes "verbatim" a checkable property rather than a prompt instruction a model may quietly disregard. |
| Where the text lives | Canonical `content_blocks`, unchanged | Principle 3. Nothing is written, cached, or duplicated for this feature. |
| What crosses the wire on the turn | A `ReadingRef` — scope, page range, counts — not the text | A chapter is 30–120 KB. Persisting it into `conversation_turns` would copy canonical content into derived storage and make every conversation reload carry it. The reference re-resolves against the source instead. |
| Delivery | Installments the reader advances, ~6 000 characters each | The whole chapter is available; it arrives the way a reader consumes it. Dropping 90 KB into a message list is not "chat-like readability", it is a wall, and on mobile it is also a scroll-performance problem. |
| Segmentation unit | One canonical block, split further only at blank lines the block already contains | A blank line inside a stored block is a paragraph boundary the parser preserved. Splitting there changes no characters. Splitting anywhere else — on sentences, on a length budget — would be reflowing the author's text, which is the thing this feature exists not to do. |
| Running heads and footers | Omitted | `NON_CONTENT_CATEGORIES` — headers, footers, page-break markers. They are page furniture, not the chapter, and interleaving "Chapter 3 · 71" every few paragraphs is exactly the mobile reading experience being fixed. The count of what was dropped is reported on the reference so the omission is visible rather than silent. |
| Figures | Rendered in place, from the existing image endpoint | A figure is part of the chapter. Decorative blocks the captioner marked at ingest stay excluded, on the same rule the answer gallery uses. |
| Typographic setting | From the parser's own block category — `Title` becomes a heading, `ListItem` a list, `FigureCaption` a caption, `Formula` a monospaced block | Measured on the corpus: 7 545 `Title` and 10 923 `ListItem` blocks. Set as undifferentiated paragraphs, a paper reads as one 258-paragraph slab with its own section headings buried in it. This adds no judgment of its own — the classification is the parser's, it was made at ingest, and the *text* is identical either way. `UncategorizedText` falls through to a paragraph: 18 133 blocks of it are ordinary prose the classifier declined to label. |
| Rotated margin stamps | A run of four or more consecutive single-glyph blocks on one page is omitted, and counted | The arXiv identifier printed down the side of a preprint's first page reaches the layout parser as one block per glyph; 5 181 single-character blocks exist across this library. Rendered faithfully that is eleven one-letter paragraphs above the title. It is page furniture in the same sense a running head is, and it is omitted on the same terms — reported in `omitted_block_count`, never silently. The threshold is the *run*, not the length: a lone short block is far more likely to be an equation fragment in real text, so it is kept. |
| Tables | Rendered from stored HTML, horizontally scrollable | `table_blocks.html_content` already holds the structure; the flat text is the fallback. |
| Citations | None | A verbatim passage makes no claims, so there is nothing to ground. Its provenance is its scope, which the turn records in `resolved_scope`. An answer that carried citation markers over quoted source text would be asserting that the source supports itself. |
| Page numbers | The printed number where it is known, the PDF page otherwise | `ResolvedScope.printed_page` already measures the offset for scans. A reader jumping to the PDF needs the number they will see. |

## Request grammar

Parsing follows the correction already recorded in `study/request.py`: a
grammar, not an enumeration of reported phrasings. A verbatim request is a
**marker** wrapped around a **scope phrase**, and the scope phrase is handed to
the existing scope grammar unchanged.

```
read | show | give me | open | display | render   <scope>   in full
                                                            verbatim
                                                            word for word
                                                            in the chat
                                                            as a chat version
                                                            unabridged
```

and the possessive form, `<verb> the full text of <scope>`.

Because the inner phrase is parsed by the same patterns as `summarize
<scope>`, every scope form works the day this ships: `chapter 3`,
`chapter 3 of ddia`, `section Storage and Retrieval in chapter 3`,
`@[Attention Is All You Need]`, `this paper`, `the document`.

`read <scope> aloud` is not in the marker set and does not match.

## Route

A new route, `verbatim_reading`, sits beside `hierarchy_summary`. It resolves
its scope exactly as a summary does — the same deterministic resolver, the same
book narrowing, the same clarification when a multi-book selection makes
"chapter 3" ambiguous — and then diverges at execution, where a summary calls a
model and this loads content.

```
plan_turn ─ verbatim_reading ─→ execute_hierarchy ─→ update_state
```

The grounding ladder does not widen a verbatim request, for the same reason it
does not widen a summary: the request named its own scope. Searching further
would answer a question nobody asked.

## Contracts

`TurnResult.reading: ReadingRef | None` — set on this route only, null
everywhere else, so no existing consumer changes.

```
ReadingRef {
  book_id, book_title, node_id, kind, display_path,
  start_page, end_page, printed_start_page, printed_end_page,
  total_segments, total_characters, omitted_block_count
}
```

`GET /api/books/{book_id}/passage?node_id=&offset=&max_characters=` returns
whole segments up to the character budget, plus `next_offset` (null at the
end). Owner-scoped like every other book endpoint. Segments are:

- `heading` — a node title, or a `Title` block one level under it
- `text` — one paragraph, verbatim
- `list_item` — consecutive ones are grouped into a single list by the client
- `caption`, `formula` — set apart, text unchanged
- `table` — HTML plus flat-text fallback
- `figure` — a `FigureRef`, rendered by the component the answer gallery uses

The endpoint is a pure function of canonical storage: the same offset returns
the same segments until the book is re-ingested.

## Interface

`ReadingPassage` renders under the turn's one-line header. It is the only
surface in the conversation that is typeset for sustained reading rather than
for scanning an answer: serif prose stack, a measure that holds near 66
characters, line height 1.7, and a page tick in the margin whenever the page
turns.

Progress is stated in the reader's terms — "page 71 of 104" and a share of the
chapter — because "installment 3 of 9" is an implementation detail. Continuing
is one control, at the end, and it keeps focus so a keyboard reader is not
thrown back to the top.

## Evaluation

This route has no model call, so the failure modes are not hallucination or
citation drift. What must hold instead is reproduction:

1. Concatenating every segment of a scope, in order, reproduces every content
   block of that scope's subtree, in TOC and block order, with no character
   changed inside a block.
2. Paginating the same scope end to end yields exactly that concatenation —
   no segment repeated, none skipped, at any budget.
3. Only `NON_CONTENT_CATEGORIES` blocks, captioner-marked decorative images,
   and margin-stamp runs are absent, and `omitted_block_count` equals the
   number absent.
4. No segment's text is a single character — the check that would have caught
   the margin stamp before real data did.

These are properties, so they are tested as properties over a real ingested
book rather than against a frozen expected string.

The route is also tested with every model constructor replaced by a stub that
raises. "No model is called" is the central claim of this feature, and it is
the kind of claim that decays quietly the first time someone adds a
convenience call, so it is asserted rather than described.
