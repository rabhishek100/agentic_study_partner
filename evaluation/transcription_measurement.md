# Transcription engines: measured, 2026-08-02

A generative transcription sits inside the canonical layer, where every citation
resolves. That placement was argued for and never measured. This is the first
measurement, and it is **partly invalid** — the part that is invalid is stated
first, because quoting the headline number without it would be misleading.

Reproduce with:

```bash
uv run python -m scripts.evaluate_transcription select --gold gold.json
uv run python -m scripts.evaluate_transcription transcribe --gold gold.json
uv run python -m scripts.evaluate_transcription score --gold gold.json
```

## The flaw

The reference was built in two ways. Where Gemini and Qwen agreed closely
(31 of 39 pages), the adjudicator took **Gemini's text verbatim** as the
reference. Where they disagreed (8 pages), it rendered the page and read it.

So on 79% of the sample, Gemini was scored against itself. Its character error
rate on those pages is 0.0000, by construction rather than by merit, and its
overall 2.65% is an artifact. The same choice inflates its table F1 to a
perfect 1.000.

This was a design error in the harness instruction, not in the adjudication.
The adjudicator followed what it was told, and told us plainly what it did.

## What the numbers do support

Tesseract against a reference derived from two independent vision models and
adjudicated where they disagreed. This comparison is sound: the reference owes
nothing to Tesseract.

| Engine | Pages | CER | WER | Table cell F1 |
|---|---:|---:|---:|---:|
| Gemini 3 Flash | 33 | *0.0265* | *0.0313* | *1.000* |
| Qwen3-VL-32B | 33 | 0.0487 | 0.1067 | 0.553 |
| Tesseract 5.5.2 | 33 | **0.3258** | **0.4222** | 0.000 |

*Italicised figures are self-referential; see above.*

By page kind:

| Engine | Prose | Table | Formula | Pathological |
|---|---:|---:|---:|---:|
| Gemini | *0.087* | *0.000* | *0.000* | *0.016* |
| Qwen | 0.051 | 0.031 | 0.026 | 0.105 |
| Tesseract | 0.290 | 0.243 | 0.271 | 0.586 |

Three things survive the flaw:

**The deterministic engine is far behind.** A third of its characters are wrong
on ordinary prose and well over half on the degraded pages. Whatever the exact
gap between the two vision models, neither is in the same range. This is the
finding that justifies a generative stage existing at all.

**Tesseract scores zero on tables because it cannot represent one.** It emits
no table markup, so every cell is a miss. That is a capability difference
rather than a reading error, and it is why the plain-OCR path could never have
produced the printed contents page that gave two of these books their chapters.

**No engine fabricated anything.** Zero spans of twelve or more consecutive
words present in a reading and absent from the reference, across all three
engines and all 39 pages. This was the risk that motivated the fabrication gate,
and at this threshold on this sample it did not occur.

## What the numbers do not support

**Any ranking of Gemini against Qwen.** On the 8 pages where the reference was
independent of both, Qwen scored *better* — CER 0.170 against 0.219 — which is
the reverse of the headline. Only 4 of those pages carried enough prose to
score, so this is not a finding either; it is an absence of one.

## Qualitative findings, which are stronger than the table

The adjudicator inspected 8 pages closely and reported failure modes that no
error rate captures:

- **Gemini truncated a page.** On book 536 page 3 it stopped mid-sentence and
  dropped an entire section — a bullet list, an invite link, a QR code. Qwen
  had the full text. A truncation is invisible to CER measured against the
  truncated text, which is precisely the trap the reference construction fell
  into.
- **Gemini refused a page.** Book 536 page 100 is a photograph of a keyboard
  that ended up in the scan. Gemini returned meta-commentary explaining it
  could not help, which is not a transcription. Qwen described the image.
- **Qwen invents structure.** On several figure-heavy pages it produced
  detailed SVG reconstructions of charts — specific shapes, colours, rotations,
  coordinates — that cannot have been recovered from the image. That is
  fabrication of a kind the word-level gate does not see, because the invented
  content is markup rather than prose.
- **Qwen drops data.** On a page of matrix diagrams it replaced the actual cell
  contents with vague alt text.

## How to make the comparison valid

The reference has to be independent of every engine being scored. Options, in
increasing cost:

1. Have the adjudicator **rewrite** the agreed text from the image rather than
   copy a candidate, on every page.
2. Produce the reference with a **third** model, used nowhere else in the
   pipeline.
3. Adjudicate every page from the image, which is what "hand-transcribed"
   originally meant.

Until one of those is done, the honest claim is: *the deterministic engine is
far worse than either vision model, no engine fabricated prose, and the choice
between the two vision models is unmeasured.*

## Sample

39 pages, 13 from each of the three scanned books, stratified by what the page
contains: 3 prose, 3 table, 3 formula, 4 flagged by the fabrication gate. Pages
are spread through each book rather than taken from the front, because a scan's
quality drifts. Selection uses signals stored at ingestion and is reproducible.

Six pages were reported but excluded from the aggregate for carrying fewer than
40 words of prose, where one misread word swings the rate by tens of percent.

Cost: **$0.09** for 39 pages across two hosted models.
