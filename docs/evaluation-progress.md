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
| 2 | PostHog analytics and monitoring dashboard configuration | Pending verification/commit | Frontend tests, types/build; hosted UI events and Grafana panel queries |
| 3 | Test cleanup and complete CI discovery | Pending verification/commit | Edited suites and generated-doc checks; preserve documented baseline failures |
| 4 | Evaluator integrity | Pending | No empty-citation pass or missing-gold perfect recall; judge failures retain predictions; evidence-aware judging |
| 5 | Shared five-flow manifest, resumable runner and cost guard | Pending | Coverage mapping; isolated fixture run; interrupted-run resume; generation/judging/retries share a fail-closed $2 ceiling |
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

## Workspace exclusions

At task start, `.gitignore` has unrelated local demo-video exclusions and `tmp/`
contains unrelated/unreviewed local artifacts. Preserve both; exclude them
from task commits. Local `.env` and `frontend/.env.local` contain credentials
and stay ignored. Public setup templates contain placeholders only.

## Next action

Verify and commit unit 2 (frontend analytics/build configuration), then push.
Continue with test cleanup before changing evaluators.
