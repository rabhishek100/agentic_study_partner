# Five-flow evaluation: results and improvements

1 October 2026. Resume instructions and commit checkpoints are in
[evaluation-progress.md](evaluation-progress.md). The user deferred human
calibration; all model quality scores below remain diagnostic.

## What ran

The frozen baseline attempted 50 native production workflows: 19 chat, six
summaries, 12 lecture/course questions, five revision sheets and eight
interview cases. Forty-three generated and were judged; seven failed explicitly.
The budget ledger recorded **$0.348342240**, including failed generation and
judging, with no unknown reservations. The earlier aborted transport attempt
cost $0.008091475. The baseline uses **BM25 for book retrieval**; production's
`hybrid_rerank` default is unchanged. Unbounded reranker pricing prevents a
responsible paid rerank comparison under this experiment's hard cap.

The inspectable, sanitized baseline is
[five_flow_baseline_20261001.json](../evaluation/five_flow_baseline_20261001.json).
Exact prompts, original supplied images, source snapshots, PDFs, output hashes,
receipts and judgments stay in ignored private run directories. No private
source prose or credentials were committed.

| Flow | Generated / attempted | Native median completion | Longest completed case |
| --- | --- | --- | --- |
| Chat | 19 / 19 | 8.19s | 12.11s |
| Complete summaries | 4 / 6 | 24.58s | 27.98s |
| Lecture/course | 12 / 12 | 8.95s | 9.93s |
| Revision sheets | 2 / 5 | 231.07s | 239.74s |
| Interviews | 6 / 8 | 7.07s | 8.33s |

These are small, descriptive samples of completed cases, not service SLOs or
statistical guarantees. Interview timings in this table are candidate grading;
the two ideal-dialogue cases failed. Native generation excludes browser/API
overhead. LangSmith readback supplies completion latency, token counts and
generation cost. First-content latency is unmeasured for buffered generation.
Separate Python CPU-seconds and sampled RSS exclude child renderers and remote
model compute. Provider receipts enforce the shared generation/judge budget;
they serve a different purpose from trace-backed performance attribution.

## Measured changes

| Failure | Change | Verification |
| --- | --- | --- |
| SDK selected an unpriced `/responses` transport | Explicit Chat Completions and provider reasoning payload | Actual installed SDK payload regressions; corrected baseline completed |
| Video starter questions returned HTTP 500 on a nonexistent column | Use canonical chapter `start_ms, chapter_index` | Real SQL/cache regression failed before and passed after; browser journey has no HTTP 5xx |
| Two section summaries lost their planned canonical scope | Carry owner-checked canonical IDs through execution | Same source/gold cases: 0/2 generated before, 2/2 after; valid citations and recall 1.0 |
| A supported refusal was labelled as an answer | Recognize straight/curly `isn't enough evidence` | Exact saved-output classifier replay changes false to true; zero new generation calls |

The scope rerun cost **$0.007109685**, including its prerequisite clarification
turn and judges, with no unknown receipts. The two repaired summaries completed
in 13.44s/13.57s; Luna rated both supported and 4/4 for correctness, coverage and
usefulness. This is a functional repair plus diagnostic quality evidence,
pending human confirmation. The original baseline is preserved. The refusal
replay does not establish better generated prose or reliable model routing.

## Additional coverage and experiments

The manifest now has 54 cases. Independently source-backed additions cover
paper QA, a complete paper summary, a full paper revision sheet, and the indexed
Spanner lecture through the course path. These are new coverage, not replacements
for failed baseline cases. Paper equation fidelity needs original-page review.

The next private experiment, `evaluation/runs/unit8-source-and-repairs`, has a
$1.50 hard ceiling. It tests one bounded ideal-dialogue citation repair and
raises the **experiment-only** revision context limit from 64k to 128k using
the provider's listed Luna capacity. Application defaults remain unchanged
until the result justifies promotion. Exact code/source/configuration
fingerprints and the ledger make interrupted work resumable.

## Contract and connected-journey evidence

Final isolated backend run: **2,075 passed, 38 skipped, 956 subtests**, 130.54s.
Thirty-five skips depend on Storage configuration and previously passed in a
separate 79-case loopback Storage run; three require the evaluation corpus.
The preceding frontend verification passed **753 tests**, TypeScript and a
production build. Branch pushes do not establish hosted CI, which triggers on
main or pull requests.

The explicit Chromium → real FastAPI → isolated Postgres harness passed all
five flows: chat/summary streaming and reopen; revision enqueue/worker/PDF and
follow-up; lecture answers; course lecture exclusion and reopen; interview
pause/reload/resume, persisted grading and final report. It found no browser JS
errors or HTTP 5xx. Authentication, speech and models are fixtures; hosted
sign-in, voice quality and playback are not thereby verified. Keyboard focus,
reduced motion and 390px horizontal fit are checks, not a full WCAG audit.

Tests were audited for relevance: duplicate/dead cases were removed, meaningful
ownership/cache/queue/stream tests retained, and pytest discovery includes
previously omitted function/parameterized tests. New tests protect reproduced
failures rather than inflating counts. See [test-suite-audit.md](test-suite-audit.md).

## What remains honest and actionable

- Human review: ten outputs, two per flow, plus original equations/PDFs. The
  user explicitly deferred this; no human labels or calibrated accuracy exist.
- The baseline's RPC/OCC course members have no indexed evidence, and GFS/Raft
  are unpublished. Retrieval cannot synthesize unavailable sources. Safe
  abstention is distinguishable from the authored expected-answer mismatch.
- Three complete-source sheets exceeded the 64k context budget without
  truncation. Two completed sheets retained coverage/layout findings. Larger
  context is an experiment, not evidence that all sheets are solved.
- Two ideal interviews rejected missing required topic citations. A bounded
  answer repair is implemented and fixture-tested; its live result is pending.
- Source-selected chat can still escalate to explicitly labelled general model
  knowledge. A fresh LoRA output did so. The source-only gold expects abstention;
  report this policy mismatch rather than claiming a classification regex fixes
  model routing or changing gold to manufacture a pass.
- The complete-paper request with extra coverage instructions took top-k QA
  instead of full-scope summary. This is a newly measured routing failure.
- Repository-wide LangSmith, Grafana and PostHog implementation and hosted
  delivery checks are complete. Existing backend processes need a restart to
  pick up local exporter credentials; no hosted app deployment or live voice
  room acceptance was performed. See [observability-verification.md](observability-verification.md).

## Interview walkthrough

Explain the full canonical source → deterministic retrieval → explicit
LangGraph routing → cited output path. Show one trace and one failed case.
Distinguish locator validity from claim support and independent concept
coverage. Demonstrate the canonical-ID repair with unchanged source/gold and
before/after results. Explain why BM25 remains the affordable baseline, why
missing evidence causes explicit failures, and why human review is needed before
trusting a same-family judge. Finally show Grafana's latency/errors/CPU/RSS and
PostHog's privacy-filtered usage events; tracing, operations and product
analytics answer different questions.
