# Ingestion

Ingestion turns source files into canonical content and rebuildable search
artifacts. It uses deterministic Python pipelines, durable jobs, and explicit
publication checks; it is not an autonomous agent loop.

## PDF ingestion

### Shared entry

```mermaid
flowchart TD
    U[Reserve PDF upload] --> S[Upload private PDF]
    S --> Q[Validate and queue]
    Q --> W[Verify bytes and hash]
    W --> P[Inspect PDF]
    P --> R[Route book or paper]
```

Reservation uses an idempotency key. A ready copy with the same owner and
source hash is reused. Unreadable, encrypted, empty, and over-limit PDFs fail
before parsing. Text coverage and OCR-overlay detection are separate from
outline quality: a digital PDF can still need outline review.

Code: [upload API](../api/ingestions.py), [preflight](../ingestion/preflight.py).

### Digital books

```mermaid
flowchart TD
    P[Preflight] --> O{Safe outline?}
    O -->|yes| E[Parse approved structure]
    O -->|proposal available| H[Human outline review]
    H --> E
    O -->|no usable proposal| X[Fail hierarchy check]
    E --> C[Validate and store content]
    C --> D[Caption, chunk, embed]
    D --> V[Verify stored data]
    V --> R[Mark ready]
```

Unstructured extracts layout; the approved outline assigns page-aware blocks
to sections. An uncertain outline pauses at `needs_toc_review`. Confirmation
resumes the same job after a source-hash check. Figure-caption failure is
recorded without rejecting an otherwise usable book.

Code: [PDF pipeline](../ingestion/pipeline.py), [parser](../parsing/parser.py).

### Scanned, mixed, and OCR-backed books

```mermaid
flowchart TD
    P[Render PDF pages] --> O[Vision OCR]
    O --> C[Save page checkpoints]
    C --> T[Propose outline]
    T --> H[Human outline review]
    H --> B[Build source hierarchy]
    B --> V[Validate and store content]
    V --> I[Caption, chunk, embed]
    I --> R[Verify and mark ready]
```

OCR reads text from page images. This route also handles suspect OCR overlays
or poisoned outlines. Saved page
checkpoints avoid repeating completed transcription. Optional Tesseract
comparison adds warnings; it neither replaces vision text nor blocks ingestion.
Printed contents/page mapping are preferred over detected headings. An unusable
proposal fails. Confirmed sections are built from saved text without rerunning
layout parsing; the source hash and page mapping remain in provenance.

Code: [OCR stage](../ingestion/ocr_stage.py), [transcription](../ingestion/ocr.py),
[outline review](../ingestion/outlines.py). Model rationale: [decisions](design-decisions.md#model-defaults).

### Papers

```mermaid
flowchart TD
    P[Paper preflight] --> O{Safe sections?}
    O -->|yes| N[Keep native sections]
    O -->|no| F[Use Full paper scope]
    N --> E[Parse and validate]
    F --> E
    E --> C[Store paper]
    C --> I[Caption, chunk, embed]
    I --> R[Verify and mark ready]
```

Papers use the shared PDF pipeline but do not require invented chapters or
human TOC review. Title selection is metadata → page-one title → filename.
**Scanned papers do not enter the book vision-OCR route** and can fail the
text-quality gate. PDFs attached to videos use a separate page-extraction path.

Code: [paper routing](../ingestion/pipeline.py).

## Video ingestion

### Acquisition and transcription

```mermaid
flowchart TD
    S[YouTube URL or uploaded video] --> A[Verify and store media]
    A --> M[Read media metadata]
    M --> C{Captions usable?}
    C -->|yes| T[Store timed cues]
    C -->|no, allowed fallback| U[Transcribe audio]
    U --> T
    C -->|no permitted fallback| X[Fail transcript stage]
    T --> E[Build evidence]
```

Captions are preferred to paid transcription. Uploaded/acquired VTT candidates
need at least 90% usable coverage; fallback requires permission in request/course
settings and sufficient job budget. Audio supplies speech evidence, while video
frames supply visual evidence.

### Visual and supporting-document evidence

```mermaid
flowchart TD
    V[Video timeline] --> F[Select key frames]
    F --> O[Read frame text]
    F --> M[Analyze visuals and regions]
    P[Supporting PDFs] --> D[Extract page text]
    T[Timed transcript cues] --> E[Combine timeline evidence]
    O --> E
    M --> E
    D --> E
    E --> I[Index evidence]
```

Frame selection suppresses duplicates and caps sampling. Visual analysis adds
observations of diagrams, equations, and demonstrations. Supporting PDFs retain
page identity; a failed resource is recorded individually. Embeddings for text
and image regions depend on course settings; lexical evidence remains available.

### Publication

```mermaid
flowchart LR
    I[Indexed evidence] --> H{Minimum gates met?}
    H -->|no| F[Fail version]
    H -->|yes| S{Remaining quality gaps?}
    S -->|no| R[Publish ready]
    S -->|yes| D[Publish degraded]
```

Hard gates check canonical media, transcript coverage/gaps, transcript evidence,
and successful visual evidence. Sparse visuals, incomplete embeddings, or
missing required resources can produce `degraded` status. Re-ingestion builds a
replacement version; the published version remains available until replacement
succeeds.

Code: [stage pipeline](../video/pipeline.py), [audio](../video/audio.py),
[frames](../video/frames.py), [vision](../video/vision.py),
[resources](../video/resources.py), [evidence](../video/evidence_store.py),
[publication gates](../video/jobs.py).

## Checkpoints and publication contract

| Concern | PDFs | Videos |
|---|---|---|
| Identity | Owner and PDF hash; reviewed outline tied to source | Owner, media hash, ingestion version |
| Resume | OCR pages and committed canonical stages | Stage dependency hashes and output manifests |
| Publish | Restore canonical data; check text, chunk lineage, embeddings | Enforce hard gates; expose remaining quality gaps |

Workers renew leases and reclaim expired attempts. Reuse requires matching
input/configuration dependencies. Derived artifacts are rebuilt from canonical
records, not repaired by editing source text.

Study-time use: [study flows](flows.md). Process and retry configuration:
[operations](operations.md).
