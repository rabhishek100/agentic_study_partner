# Visual revision sheets: two-page upgrade evaluation

Date: 2026-09-07. The results below supersede the historical one-page layout.

Configuration: `revision-v3`, `revision-prompt-v9`, `html-a4-spread-v2`,
`revision-review-v3`, author and independent judge `openai/gpt-5.6-sol`.
The default model was changed after repeated cheaper-model attempts failed
explicit essential-concept coverage despite revision feedback. This is a
failure-driven choice, not a controlled model comparison.

## Final inspected artifacts

| Scope | Pages | Source units / essential concepts | Beauty | Presentation | Coverage | Conciseness |
|---|---:|---:|---:|---:|---:|---:|
| System Design Interview, chapter 1 | 2 | 29 / 28 | 4 | 4 | 4 | 5 |
| Attention Is All You Need | 2 | 35 / 27 | 4 | 4 | 4 | 4 |

Chapter PDF: 1,029,018 bytes, two targeted quality revisions, no fit/content
repair in its final generation run. Trace: `01a07848-793f-7780-ac33-f99bcdb83b1c`.
Paper PDF: 687,708 bytes. Its repaired draft and validated inventory were replayed
through the final renderer and independent reviewer after the compact-spacing
fix; that final replay passed without another model edit. Trace:
`01a07ac7-edb6-7440-a046-a326f3e570c4`. It is **not** a first-attempt success.

LangSmith reports 211.45 seconds, 191,809 tokens and $1.2110582 for the final
chapter graph. This excludes cached figure inspection and earlier tuning runs.
The paper verification replay took 38.31 seconds, 28,035 tokens and $0.174702;
it reuses the inventory and authored draft and is not an end-to-end generation
cost. These measurements come from the root traces, not hand-rolled estimates.

Both final PDFs were rendered and visually inspected. Body text is 10.5 points,
source links 9 points. Standard spacing is preferred; a compact line-spacing
profile is tried only when no standard placement fits. No text is clipped or
removed to fit, and all rendered items are checked against extracted PDF text.
The paper uses the compact profile; the chapter uses the standard profile.

Artifacts are ignored under `outputs/revision-evaluation/shipping-chapter/`
and `outputs/revision-evaluation/final-verified-paper/`. They are source-bearing
evaluation outputs, not committed test fixtures. Earlier failed attempts remain
in the evaluation directory; these two passing artifacts do not establish a
fresh-generation success rate or prove semantic completeness.

## Acceptance and failure evidence

The workflow independently inventories the complete source before composition,
inspects all original figures, and judges screenshots of the actual printable
pages. Every essential inventory concept must map to covered rendered items;
all four rubric scores must reach 4/5, with no unsupported claims or concealed
source conflicts. Coverage remains a model assessment, not proof of completeness.

Development failures that changed the implementation:

- A citation-repair response returned only one corrected concept, silently
  dropping the rest. The judge trusted that incomplete inventory and passed
  a narrow sheet. Human review rejected the result. The inventory now includes
  an exact source-unit ledger; repairs cannot drop earlier concept IDs. The
  judge must cross-check the ledger against source content, including concepts
  that the inventory missed or wrongly marked supporting. A regression test
  covers both missing units and dropped concepts.

- First-fit layouts sometimes left page 1 half empty. The renderer now compares
  six placements per spacing profile and selects the most balanced valid spread, and a useful
  original is required when the source has one.
- Long repeated node names crowded the overview. A numbered component key
  preserves each node and relationship once; whole notes move between pages
  before the author is asked to compress. Body type stays at 10.5 points; citations increase to 9 points. Inline bold
  note headings make room for larger original figures without smaller text.
- Whole-sheet quality rewrites regressed previously covered details. Quality
  revisions now return targeted note/diagram patches; unedited content and
  ledgers are preserved deterministically, and the full sheet is rejudged.
- Eight notes conflated distinct mechanisms. Up to twelve allow separate
  attention roles or operational concepts when needed.
- Review feedback now includes exact partial/missing concept rows, not only
  broad revision instructions. This addresses drafts that repeated vague
  paraphrases instead of naming inputs, outputs, and conditions.
- Inventory suggestions occasionally imported external terminology corrections.
  The inventory and judge now explicitly require both sides of a conflict to
  come from supplied evidence; generic terminology corrections are excluded.
- The paper's conflicting EN–FR BLEU values (41.0 prose versus 41.8 table)
  must be disclosed. Faithfully disclosing a source conflict is accepted;
  the reviewer must not demand an invented resolution.
- A rigid citation-intersection check rejected concepts repeated on different
  pages. Exact marker validity remains deterministic; the independent
  full-source judge checks entailment and concept coverage.
- All 25 chapter originals and all 7 paper originals were inspected in batches.
  Inspections were cached locally while tuning layout and prompts; the final
  development runs reuse those readings. Production has no inspection cache
  and inspects every canonical original on generation. The complete gallery
  accompanies the summary in the app; selected originals appear in HTML/PDF.

The two sources are development/smoke cases, not held-out gold examples.
No population-level success rate, calibrated beauty score, or measured
improvement over existing chapter summaries is claimed. Bounded failures
preserve previous saved versions. Very large scopes still fail explicitly
when complete evidence exceeds the configured context budget.

## Automated verification

40 backend tests pass across revision contracts, full-figure inspection,
review rejection/revision bounds, HTML escaping, overflow detection, durable
jobs, and worker isolation. Six revision UI tests pass, including HTML
sandboxing, downloads, full gallery, and reopening without regeneration.
Frontend typecheck, design-token lint, and isolated production build pass.
Rendered chapter and paper drafts were visually inspected at A4 size.

---

# Revision sheets: initial implementation evaluation (historical)

Date: 2026-09-05–06. These measurements used the local library; this initial version was subsequently deployed.

## What was measured

Two complete canonical scopes were exercised through the real durable job,
worker, validation, PDF-rendering, and atomic-publication path. Source IDs are
specific to this local snapshot. These are tuning/smoke examples, not a frozen
held-out gold set. No improvement over Quick chapter summaries is claimed.

| Scope | Canonical page/node units | Final PDF | Content / fit repairs | LangSmith elapsed | Tokens | Traced cost USD |
|---|---:|---:|---:|---:|---:|---:|
| Alex Xu, *System Design Interview*, chapter “Scale from Zero to Millions of Users” (book 523, chapter 2531) | 29 | 81,838 bytes; one A4 page | 0 / 0 | 24.98 s | 28,463 | 0.00969105 |
| *Attention Is All You Need* (paper 547, entire canonical paper) | 35 | 282,358 bytes; one A4 page | 0 / 0 | 29.39 s | 24,311 | 0.00818103 |

Configuration: `openai/gpt-5.6-luna`, `revision-prompt-v5`, `revision-v2`,
`a4-v3`, 64,000-token configured context budget. PDF dimensions are
595.276 × 841.89 points, with 12 mm margins, 10-point body/diagram text,
and 8-point source locators. Both final PDFs were rendered to images and
visually inspected for clipping, number collisions, and legibility.

Trace IDs:

- Chapter: `01a0734c-7465-7770-86fb-27385e886f71`.
- Paper: `01a0734c-7465-74e0-9dcb-29e92b617baf`.

Timing, usage, and cost above come from LangSmith root runs via `read_run`.
They cover the traced compose/validate/render workflow, not queue waiting,
initial source/image loading, or all earlier tuning attempts. The chapter
uses 25,752 input / 2,711 output tokens; the paper uses 21,763 / 2,548.

Generated source-bearing artifacts remain ignored under
`outputs/revision-evaluation/release-system-design/` and
`outputs/revision-evaluation/release-attention-paper/`. Each has `sheet.pdf`,
`sheet.json`, source locators, fingerprint, inspected/uninspected figure IDs,
saved version ID, and `metrics.json`. They are not fixtures or committed source.

## Failures that changed the implementation

- A 450-word target overflowed fixed text regions on real material. The prompt
  now targets 300–350 words, with a hard maximum of 450. Whole note blocks can
  move between columns; text is never reduced below its font floor.
- Requiring every ledger page to appear in its mapped printed note turned
  repeated material into a bibliography. Printed claims now cite their
  strongest passages, while every canonical source unit keeps a disposition.
  This ledger is explicitly a model assessment, not a completeness score.
- Two chapter attempts failed because ledger pointers named concept inventory
  IDs instead of rendered items. Error feedback now identifies bad and allowed
  IDs. A deterministic resolver follows an unambiguous concept’s existing
  item map; missing targets still fail. This resolution is provenance-counted.
- Diagram edge numbers collided in the narrow inter-row corridor. The renderer
  now distributes node ports and selects noncolliding number positions on
  their edges, rejecting graphs that cannot be labelled clearly.
- Repeated request keys that joined an active job could enqueue another job
  after completion. Request aliases are now persisted, including cached reads.
- Worker regression tests initially consumed unrelated local video jobs.
  They now isolate video/deck/revision queues and maintenance, with an explicit
  four-queue rotation test. The final targeted run passes.

These failures remain visible as failed local jobs or earlier evaluation
artifacts; a final successful example does not erase them or establish a
population-level success rate.

## Source spot checks and remaining quality limits

The chapter preserves separate tiers, replication’s read/write split, caching
and CDN, externalized state, geography, queues, sharding, and their trade-offs.
Checks used the canonical passages on PDF pages 8, 12–18, 20–25, and 28–30.
Observability and automation survive only as recall cues in the final sheet;
their mechanisms are underrepresented compared with page 26.

The paper preserves encoder/decoder computation, attention and its equation,
position, masking, parallelism/quadratic complexity, training, and reported
translation results. The cited architecture and masking passages on pages
3–6 and optimizer/results passages on pages 7–8 were inspected. The simplified
final diagram leaves shifted decoder inputs to the recall cue rather than
showing a separate input branch. The attention equation is repeated in a note
and the equation field despite prompting against duplication.

The canonical paper prose reports 41.0 EN–FR BLEU while its parsed table reports
41.8. The generated sheet uses the cited prose’s 41.0 without surfacing that
discrepancy. This is a source-reconciliation weakness that exact-marker
validation cannot detect. A future independently annotated comparison should
include this case before claiming numerical/semantic verification.

Not every source figure is inspected: image selection is bounded and sampled
across the complete source. Inspection coverage is recorded, captions are
labelled as derived discovery aids, and the model is instructed to abstain
when a missing critical visual prevents a reliable sheet. That instruction is
not an independent visual-completeness guarantee.

## Automated and interface checks

Final targeted commands:

```bash
uv run python -m pytest tests/test_revision_sheets.py tests/test_study_summary.py \
  tests/test_papers_feature.py tests/test_worker.py -q
cd frontend
npm run typecheck
npm test -- --run tests/revision-sheets.test.tsx tests/answer.test.tsx
npm run lint:tokens
```

Results: **63 backend tests and 40 subtests passed; 21 frontend tests passed**.
Type checking and token lint passed, with zero new token violations. A Next.js
production build also passed during implementation. Existing PyMuPDF/FastAPI
deprecation warnings remain.

Coverage includes complete scope selection and source fingerprint changes,
input-budget refusal before a model call, invalid citations/graphs/inventories,
bounded repairs, measured PDF overflow, duplicate requests, immutable reopen,
cancellation, expired leases, source changes during publication, owner RLS,
cross-owner PDF/API refusal, queue fairness, explicit paper/chapter submission,
stale chapter responses, citation navigation, and no chat submission on reopen.

The real reading component was visually checked using an illustrative fixture
at desktop width and a 390-pixel viewport. The document had no page-level
horizontal overflow; its diagram has a keyboard-focusable horizontal scroller.
That temporary preview route was removed. The authenticated browser remained
at sign-in, so a full signed-in modal/source/print walkthrough is still pending;
component and authenticated API tests do not substitute for that walkthrough.

The six-scope annotated gold set, held-out chapter/paper comparison against
Quick summaries, reader recall assessment, and exhaustive semantic/citation
entailment review from the feature specification remain future acceptance work.
The implementation guarantees validated structure and readable A4 fit; it does
not guarantee lossless coverage or automatically verified semantic completeness.
