# OCR ingestion: scanned and OCR-backed books

Status: specified, in implementation

Covers the document classes the first release refuses: pure scans, PDFs whose
text layer was produced by someone else's OCR, and digital sources whose
structure lives in typography rather than an embedded outline.

## Why this exists

`ingestion.preflight.require_supported` admits only digital PDFs that carry
both a text layer and a trustworthy embedded outline. Four books measured
against that gate fail it in four different ways, and the differences decide
the design:

| Source | Pages | Text layer | Embedded outline | Failure |
|---|---|---|---|---|
| PythonMastery (Beazley) | 550 | native, clean | none | slide deck; structure is in footers and font size |
| The Hundred-Page Language Models Book | 209 | ABBYY OCR | 113 entries | outline poisoned by OCR'd equations |
| Generative AI System Design Interview | 351 | none | none | pure scan, no printed contents page |
| System Design Interview vol. 2 | 427 | none | none | pure scan, clean, printed contents page |

Two of these need OCR. One needs its embedded outline discarded. One needs no
OCR at all and no model call of any kind.

## Principle

**Transcription and structure are separate stages with different trust
properties, and only one of them is generative.**

A vision model reads a page into text. Deterministic code reads that text into
a hierarchy. A human confirms the hierarchy. The model never decides where a
chapter begins, because a wrong boundary produces a confidently wrong citation
and there is no downstream check that would catch it.

## Stage A — transcription

Per page, in isolation: page image in, Markdown out. Tables as HTML, formulas
as LaTeX, figures as placeholder references.

Isolation is deliberate. One page per call with no cross-page context means a
page can be re-run alone, a bad page cannot poison its neighbours, and the
model cannot carry an error forward as established fact.

- Primary model: `google/gemini-3-flash-preview`. Measured over four
  representative pages at 300 dpi: 1,336–1,362 input tokens and 249–510 output
  tokens per page, $0.0019 per page, which puts the 778 scanned pages of this
  corpus at **$1.44**. The pre-measurement estimate was $3.11; output runs
  shorter than a full page of prose because Markdown drops the layout.
- Cheap tier: `qwen/qwen3-vl-32b-instruct` — roughly a sixth of that, used for
  development iteration and for escalating flagged pages.
- Both are configuration. A `-preview` model id will be deprecated, and a
  hardcoded one turns that into an outage.

Provider access goes through an `OcrProvider` port with an OpenRouter adapter
and a local Tesseract adapter. A second provider — FriendliAI hosts the
Qianfan-OCR specialist — is a configuration change, not a rewrite. No OCR
specialist is reachable through OpenRouter today: `baidu/qianfan-ocr-fast` is
listed with no live endpoints, and none of DeepSeek-OCR, PaddleOCR-VL,
dots.ocr or HunyuanOCR appear at all.

### Canonical status

For a pure scan the transcription **is** the canonical text. Nothing more
faithful exists, and elevating the Tesseract reading instead would make the
worse transcription authoritative and have citations quote text no reader can
see on the page.

The honest treatment is provenance, not pretence. Every page records
`ocr_provider`, `model_id`, `render_dpi`, `prompt_hash`, and `ocr_version`.
Blocks derive from that text. Re-OCR bumps `ocr_version` and rebuilds
everything derived, exactly as chunks and embeddings already rebuild.

### Fabrication gate

The failure mode a generative transcription introduces, and a deterministic
one cannot, is invention: a fluent sentence that is not on the page.

Tesseract runs on every page — it is free, local, and already installed — and
long spans in the model output with no token support in its reading raise a
per-page `fabrication_risk`. Flagged pages escalate to a second model, and
pages that still disagree surface in the review queue.

The gate **flags, it never blocks**. The threshold is a heuristic until the
gold set exists, and a heuristic that can halt a 400-page book on one noisy
diagram page trades a small risk for a certain one.

## Stage B — structure

Deterministic, and it reads Stage A's Markdown rather than the image:

- heading levels from Markdown depth plus the existing numbering patterns;
- printed page numbers from the running margins, as a **piecewise map rather
  than one offset**. Measured on the phone-scanned book, the offset runs from 8
  at the front to 1 at the back: seven printed pages are simply absent from the
  scan. A single global offset placed its last chapter seven pages wrong, and a
  citation seven pages wrong is worse than none, because it looks right. So the
  mapping is a list of anchors — pages whose number was actually read — with
  interpolation between them, and the observed offset range is reported so a
  reviewer sees that pages are missing. Anchors are chosen by how fast a number
  moves: a page number advances at roughly the rate the PDF page does, while a
  chapter number advances by one every thirty pages, and no plausible rate band
  holds both. Roman front matter is anchored separately because it restarts;
- tables into `TableBlock.html` with a plain-text fallback;
- figures cropped from the page render at the model's reported region, or
  referenced whole when it reports none, then captioned by the existing
  figure-captioning role.

Stage A marks all of this in its output — `<!-- header: -->`, `<!-- footer: -->`,
`<figure data-bbox>`, `<table>` — and Stage B reads those markers, never the
pixels. Measured note on the boxes: the prompt asks for coordinates from 0 to
1 and Gemini returns them on a 0-to-1000 grid regardless. Checked against a
real page the values are accurate and merely scaled, so the parser accepts
both conventions rather than enforcing one.

LaTeX is stripped before BM25 indexing and kept for display, so `\frac` never
becomes a search term in the chapters that are most math-heavy. `chunks` gained
a `search_text` column holding the indexable rendering when it differs and null
when it does not, which is every chunk of every natively digital book. Only the
markup is removed; the words and numbers inside the mathematics stay, so
"64 x 64 pixels" still finds the page that prints `$64 \times 64$`.

Sectioning is where transcription pays off. The PDF parser has to *locate* a
heading among positioned elements to split a page between two sections and
sometimes cannot — one unlocatable heading once cost a 279-page book its
sub-page attribution. In a transcription the heading is marked as a heading, so
the split is a string comparison, matched on words rather than characters
because an outline entry and its printed heading routinely disagree about
punctuation and whether the number is joined to the title.

Output goes through `assess_outline` and into `needs_toc_review`. Confirmation
is **mandatory for every OCR-backed book**. For these four sources that is
about ten minutes each, and it is the whole reason the citations are
defensible.

### The printed contents page

`parsing/contents.py` reads the book's own statement of its structure, which
outranks a heading sweep: it names chapters the body pages never repeat in a
recognisable form. This is also the single page where transcription pays for
itself outright — a contents page is two columns, and plain OCR reads it column
by column, returning thirteen chapter names followed by thirteen page numbers
with the pairing destroyed. The transcription returns it as a table with the
rows intact. Both the table form and the dotted-leader form are handled; depth
comes from the numbering the book prints, because indentation survives neither
OCR nor a table cell.

Entries whose printed number cannot be placed are named in the warnings rather
than guessed at, and the heading proposer remains the fallback for a book with
no listing — such as the GenAI scan, whose copy had its front matter removed.

### Poisoned embedded outlines

The Hundred-Page book's outline has `derr3 derr3 dd3 dw dd3 dw` and `dd±` as
level-1 entries — ABBYY promoted OCR'd equations into the hierarchy — while
the actual chapter headings appear nowhere in it. `outline_roles.chapter_level`
finds no chapter run in that and falls back to the depth rule, which types
equation fragments as chapters.

An outline is poisoned when **both** signals appear: at least a tenth of its
entries fail to read as titles, and some page carries a crowd of entries that
are mostly junk. Measured on that book, 19 of 113 entries fail and page 27
alone holds ten, while no legitimate page in the corpus holds more than three.
Both signals are required because either alone false-positives — a clean
fixture scores 25% junk on a copyright line that ends in a full stop, and a
chapter opening may legitimately start several subsections on one page.

A poisoned outline is discarded rather than repaired: the actual chapter
headings are absent from it entirely, so there is nothing to repair towards.
The book routes to transcription and gets its hierarchy from the printed
contents page instead.

## Routing

Preflight already measures the document classes; only the routing changes.
`SCANNED`, `MIXED`, and `ocr_backed` flow into the OCR stage and end in
`needs_toc_review`. Rejection is reserved for corrupt or genuinely unsupported
input.

Nothing digital is routed through a model. PythonMastery has a clean native
text layer, a footer that names its section on every page, and a title at a
distinct size on all 550 of them; its structure is recovered by plain Python.

## Slides

Slide decks get their own entity rather than sharing the book tables. This
departs from the `AGENTS.md` line committing PowerPoint ingestion to the same
canonical content model; the decision anticipates more decks arriving, and
`AGENTS.md` is updated rather than left to contradict the code.

A slide averages 370 characters. As a retrieval unit that is a bullet fragment
with no connective prose, so chunking groups consecutive slides within a
section.

## Operational limits

The worker is 8 vCPU / 8 GB with no GPU, against a Supabase free plan capped at
50 MB per upload and 1 GB total.

- Eight parallel page calls per job, with retry and backoff. Page-level
  checkpoints, so a resumed job never re-pays for completed pages. Measured on
  a 12-page slice: 2.3 s per page cold, and a resume that reuses all twelve in
  0.04 s at no cost.
- A checkpoint is keyed on the *reading* — model id and prompt hash — not on
  the page number. A retry under a changed model or instruction re-reads,
  because mixing two engines' pages would leave a book carrying one engine's
  provenance. The first version of this looked up the hash with `getattr` on a
  provider that kept it private, which silently reduced to "no checkpoints
  match" and paid for every page twice; both identifiers are now part of the
  `OcrProvider` contract.
- A hard per-job page and token cap. Exceeding either aborts with a named
  error rather than spending silently; real dollars per book land in job
  provenance so the evaluation quotes measurements, not estimates.
- Page renders are not persisted — they are rebuildable from the source. A
  low-resolution thumbnail per page is kept for the review interface, on the
  same derived footing as chunks and inside a documented size budget.
- Two sources exceed the 50 MB upload ceiling (61.3 MB and 55.5 MB). They
  ingest through an admin path that feeds the worker from disk, and 50 MB is
  documented in the upload interface as a real product limit. Recompressing
  sources already at 110–160 dpi would degrade the input to the stage whose
  entire job is reading them accurately.

## Evaluation

Forty pages, ten per book, spanning clean prose, table-heavy, formula-heavy,
and pathological pages — bleed-through, the two-column contents page,
figure-dense layouts.

The reference transcription is **model-adjudicated, not ground truth**, and is
labelled that way with the adjudicating model id in provenance. The adjudicator
reads each page and resolves every Tesseract-versus-candidate disagreement by
zooming the disputed region, which is a stronger annotator than any single
pass. A human spot-checks five pages so the report can state a measured
agreement rate instead of asserting correctness.

Measured: CER, WER, table-cell F1, fabricated-span count, heading-match ratio,
printed-page-offset accuracy — three ways, over Gemini 3 Flash, Qwen3-VL-32B,
and Tesseract, with real cost per book.

That table is what justifies a generative stage sitting inside the canonical
layer. Without it, the layer is an assertion.

## Order of work

1. `OcrProvider` port, OpenRouter and Tesseract adapters, fabrication gate.
2. Preflight routing and the OCR job stage with page checkpointing.
3. Stage B structured extraction.
4. Poisoned-outline detector and printed-contents parser.
5. Slide entity, structure synthesis, slide-run chunking.
6. `needs_toc_review` interface.
7. Gold set and the three-way evaluation report.
