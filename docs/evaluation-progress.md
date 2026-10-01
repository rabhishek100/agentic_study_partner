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
| 6 | Simple local evidence/artifact review UI | Complete | All five artifact types; blind review; output-hash labels; save/resume/export; keyboard and unsafe-content checks |
| 7 | Connected journeys, remaining test failures and live baselines | Pending | Authenticated five-flow journeys; stream recovery; baseline results with explicit unknowns and spend |
| 7a | Test isolation and six baseline failures | Complete | Reproduction; dedicated migrated database; local Storage fixtures; privacy and queue protection |
| 7b | Independent sheet criteria and live baselines | In progress | Source-backed gold criteria; all five native flows; explicit failed/unknown spend and quality |
| 7c | Connected five-flow journeys | Pending | Frontend/API persistence, navigation and recovery with fixture providers; voice gaps explicit |
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

## Workspace exclusions

At task start, `.gitignore` has unrelated local demo-video exclusions and `tmp/`
contains unrelated/unreviewed local artifacts. Preserve both; exclude them
from task commits. Local `.env` and `frontend/.env.local` contain credentials
and stay ignored. Public setup templates contain placeholders only.

## Next action

Continue unit 7b: close independent sheet-gold gaps, run budgeted live baselines,
then unit 7c verifies connected journeys. Do not call authored-scenario interview
checks a live adaptive session/voice journey. Unit 8 measures paired improvements.
