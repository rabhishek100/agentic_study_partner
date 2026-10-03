# Test suite audit — updated 2026-10-03

Keep most behavioral tests. Reduce duplication and brittle implementation
checks, and close discovery/flow gaps before adding more small assertions.
A large passing count is not evidence of answer quality or browser integration.

## Inventory and scope of this audit

Before cleanup, pytest collected **2,037 backend cases** across 160 test files.
The frontend has **753 cases in 88 files**, verified in the observability work.
Parameterized cases differ from the number of test function definitions.

This audit inventoried all test files and Python test definitions, checked
exact duplicated bodies/names and CI discovery, and read representative tests
for each major subsystem. It is not a measured line/branch coverage report or
a claim that every assertion received a manual review. No live model evaluation
or paid inference was used.

## Concrete cleanup completed

| Finding | Change | Why |
| --- | --- | --- |
| `test_book_source.py` defined `ViewerCopyPreferenceTests` twice | Removed the identical overwritten class and kept one CLI guard at the end | Removes 64 lines of dead duplication; collected coverage is unchanged |
| Main CI used unittest; only two pytest files ran separately | Use one pytest run with the voice extra installed | 89 parameterized/function cases in 10 other files were silently omitted from CI; discovery now includes both unittest and pytest |
| The DuckDuckGo smoke test used the network and accepted an empty result list | Exercise the real HTML parser with a MockTransport fixture | Checks result mapping, redirect resolution, rank and result limiting deterministically |
| Four transcript rollback/ownership tests expected zero rows across all owners | Count rows for both fixture owners | Preserves unauthorized-write/rollback detection while ignoring unrelated local data |

The cleanup deliberately does not force a lower test count. Three duplicated
method bodies in the overwritten class never ran twice; reading and watching
sessions have identical-looking tests that exercise different implementations
and should both stay.

## Keep

- Ownership/auth/RLS, signed media access, input rejection and source/version
  boundaries. Mocked API tests and real database RLS tests cover different
  defenses; neither substitutes for the other.
- Grounding, source scope, citation resolution, insufficient-evidence refusal,
  complete chapter coverage and retrieval routing/retry behavior.
- Ingestion idempotency, cancellation, atomic persistence, leases and recovery.
  These guard data loss and stuck demos, not hypothetical scale.
- Stateful UI tests: stale responses, source switches, side chats, keyboard
  interaction, loading/errors, stream cancellation and voice command ordering.
- Evaluator scoring/report tests, including failure accounting and escaping.
- The new LangSmith/OpenTelemetry/PostHog tests: real SDK nesting, streaming
  lifetime, redaction, trace IDs and local OTLP wire export protect behavior
  that a rendering test or mocked function-call assertion cannot establish.
- Identity isolation, notification project hierarchy, concurrent ideal reuse,
  long proxy responses, full-source summary repairs, source-bound images and
  browser cleanup. These protect user scope, complete evidence and finished demos.
- Paid budget interruption/resume, preserved outputs and safe reporting when
  hosted trace lookup fails. Ordinary tests use fixture transports, not inference.
- Narrow static guards with a demonstrated operational purpose: Docker import
  boundaries, model defaults/cost choices, provider tracing coverage and
  generated documentation drift. Static checks are not automatically waste.

## Consolidate or replace next

| Candidate | Recommendation |
| --- | --- |
| Frontend `states.test.tsx` assertions on exact Tailwind classes | Keep ARIA/disabled/indeterminate behavior; replace exact styling assertions with computed-style/contrast/focus checks in a small real-browser accessibility smoke |
| `compact-frame.test.tsx` assertions on visibility classes and parent layout structure | Keep menu actions/keyboard behavior; replace layout/class checks with desktop/mobile browser assertions that controls remain reachable |
| `compact-type-floor.test.ts` regex over TSX class strings | Keep as a cheap lint guard for now; real mobile viewport/focused-field font-size checks should eventually own the behavior |
| `test_environment_contracts.py` literal substrings in shell scripts/config | Retain safeguards against wrong environments and secret copying; move syntax/pinning checks to lint and test dangerous decisions with fixture commands before removing substring checks |
| Repeated single-value formatting/pure-function tests | Table-driven cases can reduce setup and prose; retain boundary inputs and failures, rather than shrinking the reported number of cases |
| Repeated SQL/schema fixtures | Share fixture builders where setup is truly identical; retain FK/check/RLS tests because they exercise database enforcement |

These are candidates, not blanket deletions: remove an old assertion only after
its replacement demonstrates the same failure. The current class checks do not
prove WCAG contrast, responsive layout or visible keyboard focus in a browser.

## Original gaps and subsequent closure

The list below records the audit's original gaps. Items 1, 3 and 4 now have
the connected journey harness, complete source/image capture, evidence-aware
judging, and real sync/async budget/resume regressions. Stream framing,
cancellation and source-switch protection retain their dedicated tests; the
connected harness exercises SSE and persistence/reopen, rather than every
disconnect/source-switch permutation. Keyboard/reduced-motion/mobile checks
cover part of item 5; actual contrast and focus return remain follow-ups.

1. **Five connected browser journeys**: authenticated chat with persisted answer
   and working citation; selected complete chapter summary; dedicated video and
   course study with timestamp/source navigation; generated/reloaded/downloaded
   revision sheet; interview start → answer → feedback → recap. Use a migrated
   test database and fixture model/media adapters, so there is no paid inference.
   Existing jsdom tests mock the API and cannot prove frontend/API contract,
   streaming proxy behavior, real layout or a full user journey.
2. **Stream lifecycle integration**: response split over network reads, a final
   SSE error under HTTP 200, disconnect/stop, and switching the source during a
   pending answer must leave a recoverable UI and correctly finalized traces.
   Framing, backend streaming and component state have local tests, but no
   connected browser/network scenario was found.
3. **Evaluator integrity** before trusting the new baseline: an answer with no
   citations cannot pass grounded citation checks; wrong page/timestamp and
   unsupported claims fail against supplied source evidence; missing expected
   evidence is not perfect recall; judge failure preserves the generated output
   and remains distinct from application failure. In `evals.multiturn`,
   the original empty-citation, missing-gold and judge-failure defects are now
   fixed for book/video evaluators with regression tests. Complete source/image
   evidence, budget integrity and native five-flow baselines are now implemented
   and verified separately from deterministic CI.
4. **The $1–$2 evaluation budget**: once the shared budget guard is implemented,
   test that generation, judging and retries share the cap; insufficient/unknown
   price or usage cannot silently bypass it; stopped runs retain completed cases
   and resume without paying for them again. A provider-quota error parser is
   already tested, but it is not an experiment budget guard.
5. **A small browser accessibility check**: keyboard/focus, dialog close/focus
   return, actual contrast and usable compact viewports across the five journeys.
   This should replace some class-level tests and protect the real demo.

Do not add live LLM quality assertions to ordinary CI. Deterministic tests prove
contracts and failure handling; curated quality evals measure groundedness,
coverage and usefulness under the separately budgeted run. Neither replaces
the other.

## Simple execution policy

- During an edit, run the tests for the changed behavior and its caller, plus
  typecheck/build when the interface contract changes.
- CI runs all deterministic Python and frontend tests; pytest is the single
  Python entry point. Run database/queue tests sequentially against an isolated,
  migrated local Supabase with no competing worker. Avoid pytest-xdist here.
- Keep slow real-media/render/process tests, but move them into a named slow
  group only if measured duration becomes a problem. Do not build a new test
  orchestration framework or coverage-percentage gate merely to manage counts.
- Quality evals and hosted delivery checks run explicitly with their own budget
  and credentials. Skipped/unknown cases must be visible in reports.

## Validation

The selected runtime's isolated suite passes **2,181 tests + 973 subtests**,
with 38 skips and no failures. The unchanged frontend's verification passes
753 tests, types and build. These are local checks, not a hosted CI result or
exhaustive coverage. [Quality results and limits](evaluation-round3-results.md)
are measured separately from deterministic test contracts.

The edited book-source, external-search and transcript suites passed **44 cases**.
All **101 standalone pytest cases** passed, including the 89 previously absent
from CI. The full pytest run collected the same 2,037 cases and finished in 329.91 seconds:
**2,029 passed, 6 failed, 2 skipped** (plus 952 subtests). The four transcript
failures from the earlier baseline are resolved. The remaining six match the
previously reproduced baseline failures: two suggested-question-cache permission
checks, caption source selection, cited-document image rendering, expired-lease
fencing, and atomic video upload. Retain and triage these checks; cache-access
permissions in particular protect private data. This run does not prove CI
passes against a fresh Supabase instance. Frontend code
was unchanged by this audit; the preceding run passed all 753 frontend cases.

API catalog and all five generated LangGraph diagram checks passed; CI YAML
parsed and the edited tests compile. The test count did not grow: the live
search case was strengthened and dead duplicated code removed.

## Test isolation and baseline triage

The six known failures were reproduced, then all passed against the new migrated
`study_partner_eval_test` database with hermetic media defaults. Three local-media
fixtures had inherited `VIDEO_MEDIA_BACKEND=r2` from `.env`; the expired-lease
test's global reclaim saw stale application jobs; two cache tests correctly
identified restored grants inconsistent with the existing privacy migration.
No application generation/lease assertions were weakened.

`tests/conftest.py` establishes local filesystem/Supabase defaults before API
imports load `.env`, clears ambient inference keys, disables hosted telemetry,
rejects hosted Storage endpoints and honors `TEST_DATABASE_URL`. Queue modules
are checked once before fixtures run; an existing book/video queue causes an
explicit skip rather than a foreign claim. An idle external worker can still race
tests, so a dedicated database with no worker remains the correct setup.
Two OCR constructor tests now scope their fake key with `patch.dict`, rather
than leaking it into later tests with `setdefault`.

The application queue audit found no committed events/updates from this run for
the configured owner; its last video-job updates remained 2026-09-07. The existing
cache-privacy migration was reapplied to the local app database and direct
authenticated access was verified rejected, including revoked truncate grants.
No canonical content or existing application jobs were reset.

Verification: the isolated full run passed **2,059 cases and 952 subtests** in
112.13s, with 38 explicit skips. The 35 Storage-dependent skips subsequently
passed against the existing loopback Supabase Storage service in a targeted
79-case run (30.94s). Its uploads/deletes were limited to unique fixture-owner
paths. The remaining three corpus-only checks require backfilled gold data and
remain separate from deterministic CI. The six-failure group plus queue guards
passed 45 cases (8.69s). These results verify the environment repairs; they do
not establish live output quality or a hosted CI run.

The subsequent evaluation improvements were checked again at `25481f4`:
**2,075 passed, 38 skipped, 956 subtests**, 130.54s. Additions protect the real
video starter-question SQL/cache boundary, planned canonical scope ownership,
contracted refusals, bounded ideal citation repair and entire-paper adapter
scope. An explicit isolated Chromium/FastAPI/Postgres harness now verifies
all five selected journeys, including course exclusion and interview recovery.
It is run separately from CI and uses fixture identity/models/speech. A newly
discovered complete-paper routing issue has a reproducing regression awaiting
repair; this checkpoint is not claimed as a pass for that later test.

Final runtime verification after paper routing, 128k sheet capacity and full-source
summary repair: **2,080 passed, 38 skipped, 962 subtests**, 129.73s, with 12
warnings. Connected journeys passed again after those changes with no browser
JS errors or HTTP 5xx. One legacy summary test's narrow repair-prompt expectation
was updated to require complete source and exact original-answer retention;
existing citation/coverage checks stay intact. A new regression verifies an
oversized repair is rejected before a second model call. No generated answer
quality threshold is substituted for the deferred human review.
