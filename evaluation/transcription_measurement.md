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
every engine. 15 of the 39 selected pages qualify; 13 of those carry enough
prose to aggregate.

The first run of this evaluation did not make that distinction. Wherever two
engines agreed, it took one engine's text as the reference — so that engine was
scored against its own output on 31 of 39 pages and returned a character error
rate of exactly 0.0000. A number produced by construction reads exactly like a
number produced by measurement, which is what made it dangerous. The scorer now
refuses any reference derived from a candidate, so the mistake cannot be made
silently again.

The 24 excluded pages still hold candidate-derived references. Rebuilding them
needs someone to read 24 page images; three attempts to have an agent do it
stalled, so they remain excluded rather than quietly counted.

## Results

13 pages, references written from the image.

| Engine | CER | WER | Table cell F1 | Fabricated spans |
|---|---:|---:|---:|---:|
| Qwen3-VL-32B | **0.1685** | **0.1874** | **0.948** | 0 |
| Gemini 3 Flash | 0.1807 | 0.2114 | 0.917 | 0 |
| Tesseract 5.5.2 | 0.3386 | 0.4007 | 0.000 | 0 |

By page kind:

| Engine | Prose | Table | Formula | Pathological |
|---|---:|---:|---:|---:|
| Qwen3-VL-32B | **0.051** | 0.019 | 0.022 | 0.906 |
| Gemini 3 Flash | 0.161 | 0.020 | 0.018 | 0.716 |
| Tesseract | 0.195 | 0.330 | 0.176 | 0.954 |

## What this supports

**The deterministic engine is roughly twice as wrong as either vision model**,
and cannot represent a table at all — its cell F1 is zero because it emits no
table structure, which is a capability difference rather than a reading error.
That gap is what justifies a generative stage existing, and it is why the
plain-OCR path could never have read the printed contents pages that gave two
of these books their chapters.

**No engine fabricated prose.** Zero spans of twelve or more consecutive words
present in a reading and absent from the reference, across three engines and
every scored page. That was the risk the fabrication gate exists for.

**The expensive model is not measurably better than the cheap one.** Qwen3-VL
edges Gemini on every aggregate here, and is three times better on prose
specifically. At n=13 that is not a finding that Qwen is better — it is a
finding that *the premise behind choosing Gemini as primary is unsupported*.
Gemini was selected on OCR Arena standing and costs roughly six times as much
per page. Nothing measured here justifies that.

**Pathological pages defeat everything.** 0.72 to 0.95 character error across
all three engines. These are the bleed-through and figure-only pages, and no
engine reads them well. The fabrication gate's 22% flag rate on one book is
better explained by pages nothing can read than by a model inventing text.

## What this does not support

Any confident ranking of the two vision models. Thirteen pages is a small
sample and the gap between them is a hundredth of a character error rate. The
honest statement is that they are indistinguishable at this sample size, which
is itself decision-relevant given the price difference.

## Qualitative findings

From close inspection of the pages where engines disagreed:

- **Gemini truncated a page**, stopping mid-sentence and dropping a whole
  section. This is the likeliest explanation for its prose CER being three
  times Qwen's here.
- **Gemini refused a page** — a photograph of a keyboard that ended up in a
  scan — returning meta-commentary rather than a transcription.
- **Qwen invents structure.** On figure-heavy pages it produces detailed SVG
  reconstructions with coordinates it cannot have recovered from the image.
  The word-level fabrication check does not see this, because the invented
  content is markup rather than prose.
- **Qwen drops data**, replacing matrix cell contents with vague alt text.

## Sample

39 pages selected, 13 from each of three scanned books, stratified by content:
3 prose, 3 table, 3 formula, 4 flagged by the fabrication gate. Pages are spread
through each book rather than drawn from the front, because a scan's quality
drifts. Selection uses signals stored at ingestion and is reproducible.

Cost: **$0.09** for 39 pages across two hosted models.

## What would strengthen this

Rebuilding the 24 excluded references from their page images, which would take
the sample from 13 to about 35 and make the vision-model comparison worth
acting on. Until then the actionable finding is the Tesseract gap and the
absence of fabrication; the choice between the two vision models should be
treated as open, not settled in favour of the one currently in production.
