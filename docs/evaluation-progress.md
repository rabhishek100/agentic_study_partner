# Evaluation and observability delivery tracker

Updated: 2026-10-02. Branch: `codex/gpt-6-luna`.

This is the resume point for the five-flow evaluation and repository-wide
observability work. Complete and verify one dependency-coherent unit, update
this file in its commit, and push that commit before starting the next unit.
Never equate fixture tests, hosted telemetry delivery, and live output quality.

## Execution order

| Unit | Deliverable | State | Acceptance |
| --- | --- | --- | --- |
| 0 | Versioned plan and resume tracker | Complete | Plan matches confirmed five flows and $2 experiment ceiling |
| 1 | Repository-wide LangSmith and operational telemetry | Complete | SDK/context/stream/error/privacy tests; container imports; hosted delivery |
| 2 | PostHog analytics and monitoring dashboard configuration | Complete | Frontend tests, types/build; hosted UI events and Grafana panel queries |
| 3 | Test cleanup and complete CI discovery | Complete | Edited suites and generated-doc checks; preserve documented baseline failures |
| 4 | Evaluator integrity | Complete | No empty-citation pass or missing-gold perfect recall; judge failures retain predictions; evidence-aware judging |
| 5 | Shared five-flow manifest, resumable runner and cost guard | Complete | Coverage mapping; isolated fixture run; interrupted-run resume; generation/judging/retries share a fail-closed $2 ceiling |
| 5a | Durable inference budget and request capture | Complete | Sync/async/retry transport; unknown receipts; concurrency; crash/resume; no over-cap request sent |
| 5b | Coverage manifest and resumable orchestration | Complete | Five-flow mapping; durable generation before judging; source/configuration fingerprints; fixture resume |
| 5c | Production adapters and trace-backed reporting | Complete | Canonical source identity; all five output types; exact generation inputs; real LangSmith metrics |
| 6 | Simple local evidence/artifact review UI | Complete | All five artifact types; blind review; output-hash labels; save/resume/export; keyboard and unsafe-content checks |
| 7 | Connected journeys, remaining test failures and live baselines | Complete | Fixture-authenticated five-flow journeys; persistence/recovery; baseline results with explicit unknowns and spend |
| 7a | Test isolation and six baseline failures | Complete | Reproduction; dedicated migrated database; local Storage fixtures; privacy and queue protection |
| 7b | Independent sheet criteria and live baselines | Complete | Source-backed gold criteria; all five native flows; explicit failed/unknown spend and quality |
| 7c | Connected five-flow journeys | Complete | Frontend/API persistence, navigation and recovery with fixture providers; voice gaps explicit |
| 8 | Measured improvements and final report | Complete | Unchanged-source/gold rechecks, diagnostic limits, final report and recruiter walkthrough; human calibration deferred |
| 9a | Default automated artifact review | Complete | LLM scores visible by default; manual review optional; rendered PDF pages and immutable saved-output review |
| 9b | Ten saved-output LLM reviews | Complete | Two per flow; source/PDF hashes verified; no regeneration; $0.136506925 under $1 cap; hosted trace readback |
| 10 | Complete verification checklist | Complete | Ordered commands, every feature family, expected results, hosted checks and explicit coverage gaps |
| 11 | Production deployment and acceptance | Complete, bounded | Five services at d58c608; every feature family exercised, four runtime defects fixed and rechecked, production dashboard filtered; explicit device/external/quality limits in production-verification.md |
| 12 | LangSmith notification noise | In progress | Routing implemented; 38 targeted tests and full isolated suite (2,104 tests / 962 subtests, 38 skips) pass; API deployment and hosted readback pending |

The confirmed flows are chat, complete summaries, dedicated video/course study,
revision sheets, and interviews. The detailed criteria are in
[evaluation-metrics-plan.md](evaluation-metrics-plan.md). LangSmith coverage is
repository-wide and independent of this five-flow selection.

## Resume procedure

1. Read this file, root `AGENTS.md`, relevant directory instructions, and
   `git status`. Use `git log --oneline -12` to locate the committed checkpoints.
2. Continue the first unfinished unit. Do not rerun paid work merely to recover
   context: use saved bundles, source/configuration/output fingerprints and
   the recorded experiment budget. Unknown costs and unavailable sources are
   explicit incomplete results, never zero-cost successes.
3. Run the unit's acceptance checks. Record exact commands/results and any
   limits below. Stage named task files; never stage `.env`, private source
   material, generated private run bundles or unrelated changes.
4. Commit the unit and tracker together, then `git push origin HEAD`.
   If push fails, keep the local commit and record the failure before proceeding.

## Verification log

- Starting point: prior implementation is uncommitted. Hosted LangSmith smoke
  readback passed; Grafana metrics/logs/traces and five panel queries passed;
  PostHog real page views/control events passed. These establish transport,
  not full live quality. See [verification](observability-verification.md).
- Previous full baseline: backend 2,029 passed, six failed, two skipped;
  frontend 753 passed. Six backend failures concern two suggested-question
  cache access checks, caption selection, cited image rendering, expired-lease
  fencing and atomic video upload. See [test audit](test-suite-audit.md).
- At task start, backend processes had not been restarted for Grafana credentials
  and no hosted deployment, five-flow paid eval or human calibration had run.
  Paid results are below; runtime restart/deployment and human calibration
  remain separate from completed implementation.

- Unit 1: 45 targeted telemetry/tracing/container/environment/supervisor tests
  and 33 subtests passed (4.57s). Hosted LangSmith smoke read back five correctly
  nested spans with $0 provider spend. Generated LangGraph/API catalogs checked.
  Grafana three-signal readback and all five queries were verified in the preceding
  setup; no exporter code changed since that check. Checkpoint: backend tracing
  and operational telemetry commit following the plan commit `590c70b`.

- Unit 2: all 753 frontend tests in 88 files passed (24.55s); TypeScript and
  production Next build passed. Prior hosted PostHog page/control events and
  dashboard rendering remain verified; implementation has not changed since
  that hosted check. Analytics remains opt-in, with payload/profile filtering.
  Build-generated `next-env.d.ts` route-path churn was restored before commit.

- Unit 3: edited cleanup suites passed 44 cases (1.69s); voice, ideal interview,
  interview/multiturn/video evaluator suites passed 50 cases and 42 subtests
  (1.88s) through the unified pytest entry point. Generated diagrams/API checks
  passed. Previous full-suite result and six baseline failures remain visible;
  no assertion was weakened to hide them. Remote CI currently triggers on main
  or PRs, so branch pushes alone do not establish a hosted CI pass.

- Unit 4: 67 evaluator/provider/source-first/interview regression cases and
  58 subtests passed (3.41s). New failure cases cover empty/fabricated citations,
  wrong source/page/rank/timestamp/resource, missing gold and failed judges.
  Version `evidence-v2` prevents direct comparison with historical scores.
  Book evaluation now defaults to production `hybrid_rerank`. General judging
  receives available evidence/citations/prior history and explicit insufficient-
  evidence outcomes; full captured prompts/images remain unit 5 work.

- Unit 5a: 34 budget/integrity cases passed, including real sync/async httpx
  fixture transports. The guard snapshots current OpenRouter chat/embedding
  metadata, caps provider prices, saves exact bounded requests without headers,
  and persists unknown reservations across resume. No paid inference performed.

- Unit 5b: 41 runner/budget/integrity cases passed (0.64s); deterministic
  manifest regeneration check passed. Manifest: 50 cases across all five flows.
  Tests exercise completed-output reuse, failed-judge resume, budget stop after
  generation, interrupted generation, dependency recovery and tamper rejection.
  Production adapters have not run; sheet independent concept labels, several
  contract-only aspects and connected browser journeys remain explicit gaps.

- Unit 5c: 52 adapter/runner/budget/integrity cases passed (0.91s). Native
  boundaries verify saved book state, canonical locator mapping, video versions
  and media requirements, course exclusions, private PDFs/provenance and failed
  interview generation. All 50 fixture cases completed and reused on resume.
  Read-only live binding validated one matching book, four published lectures
  and one course; unavailable/unpublished course lectures are excluded explicitly.
  One live `proximity-strong` grading plus Luna judge passed: provider ledger
  $0.001332350, no unknown reservations. Hosted generation trace: 8.021803s,
  2,454 tokens, $0.000521925; all four deterministic checks passed. This is a
  transport/native grading smoke, not a five-flow quality baseline. All-flow
  live outputs and human calibration remain unit 7/8 work. Rerank pricing is
  unbounded by available metadata, so guarded baselines use labelled BM25.

- Unit 6: 58 review/adapter/runner/budget/integrity cases passed (0.94s).
  Explicit Chromium fixture smoke passed save/reload/export, preserved tab
  drafts, PDF route, keyboard focus, reduced motion, 390px layout, hostile
  text and remote-image checks. Desktop/mobile screenshots of the real grading
  output were visually inspected. The local server runs on 127.0.0.1:8766;
  the Codex panel-open request was queued. No real human quality label was
  submitted by the agent. Labels and history persist separately from bundles;
  request capture hashes now bind source/image review to the saved output.
  The earlier unit-5c smoke predates capture hashes and is marked legacy.

- Unit 7a: reproduced six failures (37 passed, six failed), then the isolated
  group passed 45 cases (8.69s). Full isolated run: 2,059 passed, 38 skipped,
  952 subtests (112.13s). The 35 Storage-dependent skips passed in a 79-case
  loopback Storage run (30.94s); only three corpus-dependent checks remain
  skipped. New pytest defaults prevent `.env` media/provider leakage; global
  queue modules check empty book/video queues before fixtures. OCR fake keys
  are scoped. Existing cache privacy migration reapplied locally; RLS and
  direct-access/truncate rejection verified. Queue audit found no recent
  committed user-job changes; existing updates remained 2026-09-07.
  Resume test DB: `study_partner_eval_test` on the local 54322 cluster, all
  54 migrations applied. Application data/jobs remain separate and unchanged.

- Unit 7b preparation: added 38 independent, source-backed sheet criteria for
  chapters 1/3/6/8/10 before inspecting generated sheets. All locators rebound
  and validated against the matching canonical book. The frozen dataset and
  bound criteria reach generation review/judging; production inventory is not
  used as independent gold. Human importance and PDF calibration remain pending.
  58 evaluator/review/adapter/budget cases passed (1.02s); manifest check passed.

- Unit 7b transport repair: the first live baseline saved a supported Chapter 3
  summary, then stopped before transmitting the planner request because the
  SDK selected `/responses`. Total $0.008091475; no unknown reservations.
  Changed book/video structured control and four legacy judges to explicit
  Chat Completions with provider reasoning in `extra_body`. Six tests inspect
  real installed SDK requests without transmission. Related 75 cases and
  42 subtests passed (4.76s). Original bundle remains immutable at
  `evaluation/runs/unit7b-bm25-baseline`; the corrected baseline uses a new
  $1.99 ceiling, keeping both attempts together below $2.
  Configuration reference: https://reference.langchain.com/python/langchain-openai/chat_models/base/BaseChatOpenAI

- Unit 7b first sample: all 11 selected native cases generated and judged,
  spanning every flow, with original images and two PDFs. Provider-reported
  generation/judge spend $0.214675225; no unknown reservations. Hosted trace
  readback verified all generation roots ended, with tokens/cost/latency.
  Sheets took 222.41/239.74s versus roughly 5–28s for other sampled cases.
  Independent sheet concept coverage was 3/4 in both diagnostic judgments;
  production findings/layout and human support still require review.
  Found a LoRA abstention classified as an answer, a course RPC retrieval miss,
  a summary citation-support concern and incomplete visual explanation.
  Sanitized immutable sample: `evaluation/five_flow_sample_20261001.json`.
  The same experiment is now resuming the remaining cases under its original
  $1.99 cap. The review server points at this corrected bundle on port 8766;
  the panel-open request was queued. No human labels were created.

- Unit 7b complete baseline: all 50 cases attempted; 43 generated/judged and
  seven failed safely (two canonical section scope resolution errors, three
  complete-source sheet context limits, two ideal-answer citation rejections).
  Provider generation/judging total $0.348342240, zero unknown reservations;
  together with the aborted transport attempt, $0.356433715. Immutable sanitized
  results: `evaluation/five_flow_baseline_20261001.json`. Completion is not a
  quality pass. Course RPC/OCC members lack indexed evidence; human calibration
  remains pending. Six pages across two native PDFs were visually inspected:
  legible, no observed clipping, but uneven whitespace and unresolved content
  findings. This agent inspection is not a human review label.

- Unit 7c: connected Chromium -> real FastAPI -> isolated Postgres passed chat
  and summary SSE/persistence/reopen, revision enqueue/worker/PDF/follow-up,
  lecture answers, course exclusion/persistence/reopen, and interview
  pause/reload/resume/grading/report. No browser JS errors or HTTP 5xx;
  reduced-motion, keyboard focus and 390px horizontal fit checked. Fixture
  authentication/models/speech and blocked hosted browser requests mean this
  does not prove real sign-in, voice quality, playback or full accessibility.
  Found and reproduced video starter questions referencing nonexistent
  `start_time_seconds`; fixed to canonical `start_ms, chapter_index`.
  Real SQL/cache regression failed before the fix; 25 video API/question cases
  passed after (5.33s). Broader five-flow/voice/telemetry contracts: 222 cases
  and five subtests passed (83.58s). Explicit journey command:
  `TEST_DATABASE_URL=...study_partner_eval_test uv run --frozen --extra voice python -m tests.check_five_flow_journeys`.

- Unit 8a: canonical section IDs now survive planner-to-execution without
  reparsing display paths. Owner, selected-book, canonical kind/identity are
  checked against live storage. Five real-database regressions include the
  conversation boundary and foreign-owner/unselected/mislabeled source cases;
  related 34 tests and three subtests passed (1.62s). Two formerly failed
  summary cases rerun with unchanged gold/source in a separate $0.30-capped
  experiment: `evaluation/runs/unit8-scope-corrected`. Generation, checks,
  diagnostic judgment and spend are retained there; original baseline intact.
  Both formerly failed cases now completed: scope/outcome/citation checks pass,
  evidence recall 1.0, diagnostic grounding supported. Total $0.007109685,
  including the required preceding clarification turn; zero unknown costs.

- Unit 8b: book and video refusal classifiers recognize straight/curly
  apostrophe `isn't/isn’t enough evidence` and `is not enough evidence`.
  Replaying the exact saved LoRA response changes missed refusal to detected
  abstention with zero model calls; this is a classification repair, not a
  claim of improved generated prose or model quality. Production-path refusal
  and affirmative-evidence regressions are included; 40 related cases and
  49 subtests passed (1.78s).

- Unit 8c: ideal dialogue now makes at most one explicit citation repair with
  the same full topic evidence and exact missing/unexpected-marker feedback.
  It regenerates the answer rather than attaching invented markers, keeps
  both call costs, traces the repair, and still rejects an invalid second
  attempt. Related 42 ideal/adapter/budget cases passed (1.82s). Paired live
  dialogue verification remains next; this test result alone is not quality.

- Unit 8d: expanded manifest to 54 cases with independently authored paper QA,
  full paper summary and full paper revision sheet, plus an indexed Spanner
  course case. Paper criteria were authored from canonical text/TOC before
  generation and all locators rebound/page-validated against the exact owned
  source hash. Spanner criteria derive from transcript 320–485s. These add
  coverage; they do not replace failed/missing-source baseline cases. The sheet
  adapter now calls the native entire-paper scope. Nineteen adapter/runner
  checks passed (0.79s); manifest regeneration and read-only source binding
  passed. Live variant output and human equation/PDF review remain pending.

- Unit 8e preparation: reporting separates exact rewrite-text mismatches from
  semantic/contract failures; absent rewrite gold stays unknown. Baseline
  outputs/scores remain immutable. Related evaluator/review/runner checks
  passed 18 cases (0.57s) before the next paid experiment.

- Unit 8e running: `evaluation/runs/unit8-source-and-repairs`, ceiling $1.50,
  experiment-only `REVISION_CONTEXT_WINDOW_TOKENS=128000`. Selected: LoRA,
  paper QA/full summary/full sheet, indexed Spanner course, book sheets 6/8/10,
  both ideal dialogues. Paper QA, paper sheet and indexed Spanner completed;
  book sheet 6 now generated where the baseline hit the context limit, with
  remaining production findings. Complete-paper prose took the wrong route;
  three real-database deterministic-routing variants reproduce that failure
  in the uncommitted `test_turn_analysis.py` test. Apply its parser repair only
  after this frozen-code experiment finishes, then rerun the unchanged paper
  case in a new directory. Fresh LoRA used labelled general model knowledge;
  its source-only gold mismatch remains visible. No gold was altered.
  The user explicitly deferred human calibration; keep zero human labels.

- Final contract checkpoint at `25481f4`: isolated backend 2,075 passed,
  38 skipped, 956 subtests (130.54s). Thirty-five Storage skips previously
  passed the targeted local Storage run; three corpus checks remain separate.
  Connected journeys remain verified at unit 7c. Newly added paper-routing
  regression is intentionally red pending the measured parser repair.

- Unit 8e complete: all ten selected outputs generated/judged for
  $0.497982575, zero unknown reservations. Both previously failed ideal
  dialogues now pass all nine deterministic dialogue/citation gates. Three
  previously blocked chapter sheets now generate at 128k but retain production
  findings; diagnostic concept coverage is not a complete-quality pass. Paper
  QA/sheet and indexed Spanner course produced native artifacts. Fresh LoRA
  fallback and complete-paper routing failures remain recorded unchanged.

- Unit 8f: whole-document grammar now recognizes entire/complete/whole scope
  and appended coverage instructions, without turning explicit part requests
  into full-document requests. Canonical-ID execution also preserves existing
  top-level preface/part/appendix chapter semantics. Both failures reproduced
  before repair; 119 related parser/analyzer/summary/ownership cases and
  55 subtests passed (4.55s). The unchanged paper case is next for paired live
  verification in a separate experiment.

- Unit 8f paired live: unchanged full-paper case now takes `hierarchy_summary`,
  validates citations and reaches recall 1.0 (previous 0.5/top-k route),
  $0.0064758, zero unknown receipts. Luna still flags contradictory addenda:
  missing heading-only Training/Results evidence prompted false claims that
  the paper lacks details already present in its child sections. This is a
  distinct measured repair-context failure, now reproduced by a regression.

- Unit 8g: promoted the measured 128k revision context capacity consistently
  in composition, figure allocation, review and follow-up, plus setup template.
  All three formerly blocked large chapters generated under the same 128k
  experiment; quality warnings remain visible, not accepted as clean sheets.
  Explicit smaller overrides and full-source fail-closed checks remain.
  Related 61 revision/budget cases passed (62.92s). The local override is unset.
  The user confirmed retaining labelled general-knowledge fallback; preserve
  that policy and distinguish old source-only gold mismatches in reports.

- Unit 8h: summary coverage addenda now receive the complete canonical source
  and preceding answer. Narrow missing-node excerpts had hidden a parent
  heading's child training/results details and provoked contradictory absence
  claims. The repair explicitly checks descendant evidence; citation/coverage
  requirements are unchanged, and the full repair prompt remains budgeted
  before transmission. The new production-path regression failed before the
  change and verifies retained source/answer after it. Live paper quality
  recheck is next; new model draws are not a controlled fixed-draft replay.

- Unit 8h verification correction: the runtime repair was pushed before one
  legacy prompt assertion was checked. That assertion expected covered source
  nodes to be excluded. It now requires full source and exact retention of the
  initial answer; coverage/citation checks remain. A new regression verifies
  that an oversized full-source repair stops before a second model call.
  Summary/routing/conversation group: 55 passed, 20 subtests (2.05s).
  The next live paper recheck uses a new run directory and unchanged gold.

- Unit 8h paired live: unchanged paper case completed in 25.68s, with the
  hierarchy route, valid citations and evidence recall 1.0. Both captured calls
  and hosted physical model spans confirm one full-source addendum; its prompt
  includes the preceding answer, which remains an exact prefix of the output.
  Luna changed from unsupported to supported, but still finds omitted future
  directions (3/3/3 diagnostic scores). This fresh draw is not a controlled
  causal prose comparison. Spend $0.01012690, zero unknown receipts.
  Sanitized four-experiment results and receipt-sum checks are saved in
  `evaluation/five_flow_improvements_20261002.json` (15 case attempts).
  All nine pages of the new chapter 6/8/10 PDFs were visually inspected:
  readable without observed layout clipping, but whitespace and incomplete
  generated sentences remain. Agent inspection is not human calibration.

- Final runtime checkpoint: isolated full backend suite passed 2,080 tests,
  962 subtests, with 38 skips and 12 warnings (129.73s). The Storage/corpus
  distinction recorded in unit 7a still applies. Connected journeys were
  rechecked after final runtime changes and all five passed, with no browser
  JS errors or HTTP 5xx. Final results, cost totals, quality gaps and interview
  walkthrough are in `docs/evaluation-results.md`.
  Final documentation checks passed: 54-case manifest regeneration, API
  endpoint catalog, all five LangGraph diagrams, 61 local links and patch
  whitespace validation. No runtime or frontend code changed after verification.

- Unit 9a (user supersedes manual-review step): automated Luna review is the
  default; manual calibration is optional, not a prerequisite. All saved
  judgments and actual human labels remain distinct. Added resumable saved-output
  judging with capture/artifact hash checks, budget-stop/retry isolation and
  full PDF-page rendering. Layout evidence is explicitly derived output, never
  original source. No pages are silently dropped. Verdicts retain failed
  contracts and unknown grounding/layout. Sixty-one evaluator/review/budget
  tests passed (1.12s); explicit browser check passed automated default and
  optional manual save/reload/export, mobile/focus and unsafe-content checks.
  Ten selected immutable cases bind successfully, exactly two per flow.

- Unit 9b: all ten saved-output reviews completed, with $0.136506925 in
  provider receipts and zero unknown reservations. Ten hosted review roots
  ended, with one physical model call each and token/latency readback.
  All five PDF pages were supplied to the judge; both artifacts are readable
  but need semantic/coverage work. Final classification: five usable under
  the rubric, five need work; this selected sample is not a population pass rate.
  Corrected a PDF applicability gate: unknown layout must not reject non-PDF
  text artifacts. Original judgments and run verdicts remain immutable; the
  public artifact records original and corrected classifications. Sixty-one
  targeted tests passed again (1.04s). Live review viewer restarted with the
  same session token and reports 43 LLM-reviewed baseline outputs, manual
  required false. Public artifact: `evaluation/automated_review_20261002.json`.
  The long-context sheet's trace estimate differs from its provider receipt;
  billing totals use the receipt. No answers were regenerated or human ratings
  fabricated. Review plan and final report now make manual calibration optional.

- Unit 10: added `docs/verification-checklist.md` and README entrypoint. Covers
  environment/test isolation, backend/frontend/contracts/Docker, both connected
  harnesses, every current product feature family, recovery/accessibility,
  repo-wide LangSmith and Grafana/PostHog, capped quality judging and acceptance
  evidence. This is a reproducible procedure, not a new execution/pass record.
  CLI options were checked locally; existing snapshots and quality findings
  are retained. No paid inference, live source mutation or service restart ran
  while preparing this guide.
  Guide validation passed: 44 local links, 11 shell command blocks and embedded
  Python syntax; bootstrap/eval/review CLI help matched the documented options.

## Workspace exclusions

At task start, `.gitignore` has unrelated local demo-video exclusions and `tmp/`
contains unrelated/unreviewed local artifacts. Preserve both; exclude them
from task commits. Local `.env` and `frontend/.env.local` contain credentials
and stay ignored. Public setup templates contain placeholders only.

## Next action

Production deployment and bounded acceptance are complete; resume from
`docs/production-verification.md`. Release `d58c608` is live on all five services;
dashboard-only changes do not need an application release. Isolated backend:
2,102 tests and 962 subtests passed, 38 skipped. Real providers exercised the five
primary flows, PDF/paper/OCR/uploaded-video ingestion, cards, reader, reminders,
settings and HTTP/live voice. All five flow-family review outputs are usable
under the bounded Luna rubric after supplying original figure pixels for the
sheet; the original unsure judgment is preserved. This is not a full gold rerun.
Recorded OpenRouter delta $0.179652 includes $0.0833288 judging; separate voice
estimates $0.012254, with partial/delayed billing caveats. Private proofs and
expiring login state stay in `/tmp/study-production-verification`, outside Git.

Remaining: user-device microphone/playback, fresh external YouTube acquisition,
actual device analytics opt-out, wider accessibility/browser coverage, broader
quality and separately scoped resilience/load tests. Four production defects
were reproduced with regressions and rechecked live: telemetry startup, voice
flush timeout, completed-upload acquisition and OCR chapter page boundaries.
Grafana now defaults to production and filters every panel; validation, hosted
queries and rendered inspection passed. One historical summary-open 500 was a
Railway internal connection timeout; retry passed, underlying cause unproven.

Automated review replacement is complete; no user rating is required. Future
live evals use the updated judge. To review saved outputs, use the committed
selection and `scripts.judge_saved_evaluations` in a new $1-capped directory;
resume only with matching reviewer code/configuration and output hashes.
The next quality priorities are sheet semantic/concept coverage, exact chat
citation alignment and summary completeness, then measured generation-latency
improvements. Preserve immutable runs, receipt budgets and optional manual labels.
