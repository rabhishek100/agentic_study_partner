# Evaluation and observability delivery tracker

Updated: 2026-10-01. Branch: `codex/gpt-6-luna`.

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
| 6 | Simple local evidence/artifact review UI | Pending | All five artifact types; blind review; output-hash labels; save/resume/export; keyboard and unsafe-content checks |
| 7 | Connected journeys, remaining test failures and live baselines | Pending | Authenticated five-flow journeys; stream recovery; baseline results with explicit unknowns and spend |
| 8 | Measured improvements and final report | Pending | Paired cases/configurations; at least one measured improvement or documented failed experiment; recruiter walkthrough |

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
- Existing backend processes have not been restarted for Grafana credentials.
  No hosted app deployment has been performed. Five-flow paid evals and human
  calibration have not run.

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

## Workspace exclusions

At task start, `.gitignore` has unrelated local demo-video exclusions and `tmp/`
contains unrelated/unreviewed local artifacts. Preserve both; exclude them
from task commits. Local `.env` and `frontend/.env.local` contain credentials
and stay ignored. Public setup templates contain placeholders only.

## Next action

Implement unit 6: a simple local review UI consuming private saved bundles.
Support grounded text, video evidence, course sources, sheet PDFs and interview
artifacts. Human labels must bind output hashes, save/resume/export and preserve
unknowns. After that, unit 7 handles connected journeys, six baseline failures
and live baselines; unit 8 measures paired improvements.
