# Transcription engines: measured, 2026-08-02

A generative transcription sits inside the canonical layer, where every citation
resolves. That placement was argued for and never measured. This is the
measurement.

Reproduce with:

```bash
uv run python -m scripts.evaluate_transcription select --gold evaluation/ocr_gold.json
uv run python -m scripts.evaluate_transcription transcribe --gold evaluation/ocr_gold.json
uv run python -m scripts.evaluate_transcription score --gold evaluation/ocr_gold.json
```

## What is scored, and what is not

Only pages whose reference was written from the **page image**, independently of
every engine. All 39 selected pages now qualify; 35 of them carry the 40 words
of prose below which one misread word swings the rate by tens of percent.

The first run of this evaluation did not make that distinction. Wherever two
engines agreed, it took one engine's text as the reference — so that engine was
scored against its own output on 31 of 39 pages and returned a character error
rate of exactly 0.0000. A number produced by construction reads exactly like a
number produced by measurement, which is what made it dangerous. The scorer now
refuses any reference derived from a candidate, so the mistake cannot be made
silently again.

The 24 references that mistake had contaminated were rebuilt by reading each
rendered page image, blind to both engines' output. One of the 24 — a dedication
page reading "To my family, with love" — came out character-identical to
Gemini's. On a six-word page that is agreement, not circularity, and it falls
below the scoring floor anyway.

## Results

35 pages, every reference written from the image.

| Engine | CER | WER | Table cell F1 | Fabricated spans |
|---|---:|---:|---:|---:|
| Gemini 3 Flash | **0.1473** | **0.1868** | **0.884** | 0 |
| Qwen3-VL-32B | 0.1480 | 0.1963 | 0.634 | 0 |
| Tesseract 5.5.2 | 0.3791 | 0.4661 | 0.000 | 0 |

By page kind:

| Engine | Prose | Table | Formula | Pathological |
|---|---:|---:|---:|---:|
| Gemini 3 Flash | 0.169 | 0.123 | 0.037 | **0.276** |
| Qwen3-VL-32B | **0.111** | **0.118** | **0.036** | 0.349 |
| Tesseract | 0.297 | 0.265 | 0.282 | 0.709 |

Tripling the sample changed the answer. At n=13 Qwen led every aggregate; at
n=35 the two are tied on CER to within a thousandth, and the lead splits by page
kind rather than falling to one model.

## What this supports

**The deterministic engine is roughly two and a half times as wrong as either
vision model**, and cannot represent a table at all — its cell F1 is zero
because it emits no table structure, which is a capability difference rather
than a reading error. That gap is what justifies a generative stage existing,
and it is why the plain-OCR path could never have read the printed contents
pages that gave two of these books their chapters.

**No engine fabricated prose.** Zero spans of twelve or more consecutive words
present in a reading and absent from the reference, across three engines and
every scored page. That was the risk the fabrication gate exists for.

**Qwen reads running text better; Gemini survives bad pages better.** Qwen is
a third better on prose (0.111 vs 0.169), which is most of what a book is made
of. Gemini is a fifth better on the pathological pages — bleed-through, figure-
only, photographed-at-an-angle. They are level on tables and formulas.

**Price is the tiebreaker, and it points at Qwen.** Gemini costs roughly six
times as much per page for a CER difference of 0.0007. Qwen stays primary;
Gemini stays the fallback and the second opinion on flagged pages, which is
exactly the split its pathological-page advantage argues for.

**Pathological pages defeat everything.** 0.28 to 0.71 character error across
all three engines. The fabrication gate's 22% flag rate on one book is better
explained by pages nothing can read than by a model inventing text.

## What this does not support

**The table F1 gap, at face value.** 0.884 against 0.634 looks decisive and
mostly is not. Both engines emit HTML tables as instructed on 8 of 9 table
pages. Two pages carry the gap:

- A one-hot matrix printed as a bordered grid, where the reference put each
  digit in its own cell and Qwen kept each row in one cell. Both are defensible
  readings of the same ink; the scorer cannot tell a convention disagreement
  from a misread. Dropping that single page moves Qwen from 0.634 to 0.786
  against Gemini's 0.876.
- A page of rating scales, where Qwen emitted no table at all.

The remaining gap is real but is two pages wide, not nine.

**Any confident ranking of the two vision models overall.** Thirty-five pages is
better than thirteen, and a 0.0007 CER difference is still noise. The honest
statement remains that they are indistinguishable on accuracy, which is
decision-relevant precisely because they are not indistinguishable on price.

## Qualitative findings

From close inspection of the pages where engines disagreed:

- **Gemini truncated a page**, stopping mid-sentence and dropping a whole
  section. This is part of why its prose CER trails Qwen's.
- **Gemini refused a page** — a photograph of a keyboard that ended up in a
  scan — returning meta-commentary rather than a transcription.
- **Qwen invents structure.** On figure-heavy pages it produces detailed SVG
  reconstructions with coordinates it cannot have recovered from the image.
  The word-level fabrication check does not see this, because the invented
  content is markup rather than prose. This is the likeliest source of its
  worse pathological-page score.
- **Qwen drops data**, replacing matrix cell contents with vague alt text.

## Sample

39 pages selected, 13 from each of three scanned books, stratified by content:
3 prose, 3 table, 3 formula, 4 flagged by the fabrication gate. Pages are spread
through each book rather than drawn from the front, because a scan's quality
drifts. Selection uses signals stored at ingestion and is reproducible.

Cost: **$0.09** for 39 pages across two hosted models.

## What would strengthen this

A larger and more varied sample. Three books by two publishers, all interview
prep, is not the range of a real library — and the two findings that would
change a decision, the prose gap and the pathological-page gap, each rest on
nine or fewer pages. A second scoring pass on tables that tolerates cell
granularity would also stop convention disagreements from reading as errors.
