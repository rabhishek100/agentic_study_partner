# Production verification — 2 October 2026

**Deployed and verified with real providers across every current feature family.**
This is bounded production acceptance, not exhaustive endpoint, security, load or
model-quality certification. Device audio, fresh external YouTube acquisition and
broader quality coverage remain open. The provider budget was $5 total, with each
evaluation experiment capped at $1–$2; no manual rating was required.

### Follow-up — 3 October: notification trace noise

API/combined worker revision `8bd52be` deployed successfully as
`b8931dac-b1c5-4bd7-90bc-fb511c32f5b7`. Routine notification GET polls and periodic
reminder checks now go to `agentic-study-partner-production-operations`, retaining
complete traces and errors. Existing evaluation and parent contexts stay intact.
Web/voice deployments remain at the accepted revision below.

Live readback verified notification trace `01a0fe3d-e748-7670-ae9b-d814ba8d0228`
in the operations project, two completed reminder checks there, and a real grounded
chat trace `01a0fe3d-e877-7c11-a968-de6d522b8493` in the main production project
with 28 spans, two LLM calls and no missing parents. Targeted checks: 38 tests /
six subtests; full isolated suite: 2,104 tests / 962 subtests, 38 skips (117.58s).
Historical notification traces are retained and can remain in old time windows
and frequent-name shortcuts; newly received polling no longer fills the main list.

## Release and recovery

- [Production application](https://web-production-8529e.up.railway.app).
- [API health](https://api-production-08e6b.up.railway.app/api/health).
- Railway project `agentic-study-partner`, environment `production`.
- **Application revision `d58c608` on all five services**, deployed from a clean
  managed worktree. Runtime API/proxy health and voice-worker environment checks
  agree with the release. Later dashboard/documentation commits need no app build.
- PostgreSQL 18.6, 60 application tables, migration head `20260914120000` already
  matched the repository; no database migration was required.
- Before release, a 432 MB custom-format backup passed SHA-256/archive checks
  (62 data-table entries). An isolated local restore passed with 61 books,
  106,269 canonical blocks, 7,842 chunk embeddings and 20,568 evidence embeddings;
  migration head and expected vector dimensions were checked. Production was
  never a restore target. The task-owned local restore database was removed
  afterward; the protected backup and private evidence stay outside Git.
- A bounded derived-data rebuild of disposable book `587` preserved the canonical
  source hash and chunk IDs/content hashes; four chunks were re-embedded at
  dimension 3,072 and all four matched the BM25 precision/recall query.

| Service | Successful final deployment |
|---|---|
| API / combined worker | `cd095a1c-91cf-4f23-a69c-a2f622a0e35f` |
| Web | `b9f49ab6-48f8-4abd-9e8e-d6a86e201fb1` |
| Adaptive voice | `a928e427-7cc3-48f4-a13d-5b38e96a0449` |
| Ideal voice | `09ea5f7f-0e71-417c-9a80-c60bbe4965f2` |
| Narration voice | `97accd4a-cfa4-47e8-8394-600a3d8b840f` |

## Live acceptance

PASS means the named scenario was actually exercised in production. It does not
mean every variation or every route in that feature passed. API probes included
expected negative cases and corrected probe-input errors; request counts are not
presented as a blanket pass rate.

| Feature | Result | Observed evidence |
|---|---|---|
| Health and authentication | PASS | Direct API and web-proxy health; authenticated reads; missing bearer 401, invalid input 422, absent source 404 |
| Ownership | PASS, bounded | A known existing foreign book returned 404 for chapters and source document; not a missing-ID substitute or complete security audit |
| Digital book / paper ingestion | PASS | Disposable PDFs uploaded, durable jobs completed, canonical content/chunks/embeddings persisted; new paper visible in UI |
| Scanned-book ingestion | PASS after fix | OCR paused for reviewed contents, resumed and published book `590`; all four chapters contain their intended text and four automatic decks are ready; reader navigates pages 1–4 |
| Chat | PASS | Real grounded answer, persisted conversation, cited sources, side-chat stream and explicit selected-source insufficient-evidence behavior; labelled general-knowledge fallback retained per user policy |
| Full chapter summaries | PASS | Whole subtree used; persisted answer with figures, 48 evidence items and 67 citations; reopened in browser and citation opened the source panel |
| Dedicated video / course study | PASS for uploaded media | Existing lecture and course cited answers; real UI streamed follow-up persisted; citation seek landed three seconds before the cited timestamp; new 10-second uploaded video published with captions, frame OCR, visual evidence and cards |
| Private video delivery | PASS | Signed playback and byte-range 206; downloaded bytes matched the original MP4; unsigned request 403 |
| Revision sheets | PASS with disclosed quality gaps | Worker finished; saved three-page PDF downloaded/rendered on all pages; UI reopened saved version, figures, citations and known gaps; grounded follow-up and reopen passed |
| Adaptive interviews | PASS | Create/start/pause/resume/answer/finish/report; completed report displays partial source coverage honestly |
| Ideal interviews | PASS | Real generation finished all 48 topics; persisted dialogue reopened and next exchange advanced in UI |
| Cards | PASS | Book, paper, video and scan generation; persisted API review and side-chat; real UI keyboard reveal/rating completed a one-card deck |
| Reader persistence | PASS | Disposable book saved/reopened page 2; scanned reader navigated to page 4; real book PDF rendered without changing its existing page-107 progress |
| Reminders | PASS, in-app only | Scheduled worker produced a notification, read/dismiss persisted; original preferences restored; no email or push sent |
| Prompt settings | PASS | API preview/save/reopen/restore; original profile restored; production studio displayed saved state and compiled preview |
| HTTP speech / dictation | PASS | Real MP3 narration and transcription through the deployed HTTP endpoints |
| Live voice transport | PASS, synthetic microphone | All three workers joined authenticated LiveKit rooms and emitted non-silent PCM; adaptive/narration transcribed injected speech and acknowledged flush; ideal completed a short utterance and replay/stop worked |
| Idempotency / cancellation | PASS, disposable data | Duplicate upload reservation reused its job; repeated cancel succeeded, retry of canceled job rejected; only our cancellation fixture was deleted |
| LangSmith | PASS across exercised flow families | Finished HTTP/job/session/RPC roots, physical LLM calls and voice metrics read back; durable job metadata links stages/attempts; production and evaluation projects separate |
| Grafana | PASS | API, worker, supervisor and three voice service families export CPU/RSS; operation counters/histograms, Loki logs and Tempo traces read back; dashboard defaults to production and all five queries filter environment |
| PostHog | PASS | Real production pageviews, actions, API responses and streamed study completion received; inspected event/profile properties omit question, answer, source text and email; normalized routes and GeoIP disable verified |
| Browser navigation / keyboard | PASS, smoke only | Books, papers, reader, lecture/course, sheets, interviews, cards and prompt studio opened; mobile/desktop checks and card keyboard controls; no complete WCAG audit claimed |
| Recovery / fault injection | PARTIAL by design | OCR review/resume, room-token expiry/refresh, cancel/replay and isolated backup restore exercised; destructive worker/provider failures remain isolated tests |

A historical pre-fix scan (`589`) remains labelled as a historical failure; its
empty last chapter is not silently rewritten. The new scan (`590`) demonstrates
the deployed repair. Existing user sources, sessions and preferences were preserved.

## Defects reproduced, fixed and rechecked

1. **Voice tracing started too late or never initialized.** All three servers
   now configure parent and child telemetry before traced sessions. Idle parent
   resource metrics also export. Targeted checks: 43 tests / six subtests.
2. **Flush failed after receiving final speech text.** Adaptive/narration workers
   propagated a five-second transcription-drain timeout, producing LiveKit RPC
   `1502`. Two regressions reproduced it. Bounded cancellation now retains delivered
   text and acknowledges flush; 45 voice/observability tests / six subtests passed.
   Real production synthetic-microphone flush then passed on both workers.
3. **Completed video uploads stranded at acquisition.** The hosted metadata
   worker excluded all acquisition, including already-uploaded sources. It now
   claims completed primary uploads without taking remote YouTube acquisition.
   The queued-upload/older-YouTube regression and 45 video tests / three subtests
   passed. Our full production upload then reached ready, cost $0.000593.
4. **Reviewed OCR headings opened on the wrong page.** Numbered/unmatched headings
   left the final chapter empty and its deck failed `source_unavailable`. Prefix
   normalization and fallback to the reviewed page boundary fix this while retaining
   legitimate preceding continuation text. Seventy-five targeted tests passed;
   the new production scan has content and ready cards in all four chapters.
5. **Dashboard mixed local and production.** Added a production-default Environment
   dropdown and label filter to every panel. Hosted/source expressions match;
   server validation, five live query readbacks and rendered inspection passed.

One real browser summary-history request failed at 17:45:45 UTC. Web logs show
connection timeouts to the API's Railway internal IPv4/IPv6 addresses. Subsequent
direct API, web proxy and UI retry succeeded. The immediate failure is established;
the underlying networking/deployment cause is not. Historical errors remain in
traces rather than being hidden. Further recurrence merits investigation.

## Measured latency, tokens and cost

These are individual production observations from LangSmith, not benchmarks,
SLAs or population percentiles. Token/cost rollups count physical model calls once.

| Observed operation | Duration (s) | Tokens | Trace-estimated model cost ($) |
|---|---:|---:|---:|
| Chat | 11.47 | 4,194 | 0.003099 |
| Full chapter summary | 27.61 | 43,121 | 0.006664 |
| Lecture study | 13.07 | 3,846 | 0.000526 |
| Course study | 4.64 | 2,859 | 0.000391 |
| Adaptive answer/evaluation | 8.99 | 6,145 | 0.000942 |
| Ideal chapter generation | 267.35 | 98,780 | 0.016276 |
| Revision-sheet generation | 268.38 | 322,997 | 0.044852 |

The latter two used 58 and 13 physical model calls respectively. Reopening stored
outputs is cheap; creation latency is the main optimization target.

Final recorded OpenRouter usage delta: **$0.179652**, including production generation
and judging. Six settled judge calls total **$0.0833288** within a single $1-capped
review experiment, included in that delta. Separate voice-metric estimates total
**$0.012254**. Canceled/partial TTS and delayed provider reporting prevent treating
these as an exact final invoice. Recorded usage is far below the authorized $5;
Railway/hosting subscriptions are outside this provider-test accounting.

CPU/RSS were observed for all six process families. Grafana is configured for
operation throughput, approximate histogram p95, failures, CPU and RSS. Five-minute
windows have few observations and deployment overlap; histogram buckets are coarse
and long jobs exceed the highest finite bucket. Use LangSmith for exact run timing;
no representative load-capacity or per-run peak-memory claim is made.

## Automated quality review

Five frozen real production outputs were judged by Luna using immutable output and
source hashes, exact captured generation inputs and agent-authored smoke criteria.
The full chapter context was paginated rather than truncated. Every sheet PDF page
was supplied. This complements the existing [evaluation report](evaluation-results.md)
and 54-case gold manifest; it is not a production rerun of that complete gold set.

Chat, summary, course study and the one answered adaptive interview turn were judged
usable/supported. The first sheet review was unsure because original figure pixels
were missing. A supplemental review supplied the original canonical figure pixels
and all three PDF pages; it judged the sheet usable/supported, 3/3/3 for correctness,
coverage and usefulness. The initial judgment is preserved. No human labels or
human calibration were fabricated, and Luna reviewing Luna retains correlated-bias risk.

Measured improvement priorities remain:

- Align chat citations with the precise definition/denominator being claimed.
- Improve summary coverage of prediction pipelines and training details; remove
  small unsupported embellishments.
- Improve sheet data/training coverage and explicitly disclose figure/prose label
  differences in the printed artifact. The UI currently exposes six known gaps.
- Reduce ideal-interview and revision-sheet creation time, with unchanged quality
  criteria and comparison inputs before changing orchestration.
- Add finer long-operation histogram buckets when accurate operational tail charts
  become necessary; do not infer long-job p95 from the current coarse chart.

## Verification and reproducible evidence

After the fixes, the isolated backend suite passed **2,102 tests and 962 subtests**,
with **38 skips**, in 115.43 seconds:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/study_partner_eval_test \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/study_partner_eval_test \
uv run --frozen --extra voice pytest tests -q
```

This was never run against production. Environment-dependent skips and the earlier
Storage/corpus distinction remain documented in [the tracker](evaluation-progress.md).
The unchanged frontend had previously passed 753 tests, types and build; all deployed
web builds succeeded. Production UI checks are separate evidence, not a substitute
for automated tests. Final API health is ready; ingestion has no queued, processing,
retry-scheduled or expired-lease jobs. Its idle heartbeat reports the last completed
job, not ongoing idle-worker liveness; newly completed jobs provide worker proof.
Read-only final checks of this run's owned jobs found four ready PDF ingestions,
one ready video ingestion, one ready sheet, eleven succeeded card jobs and the
single preserved historical pre-fix scan-card failure. No test job remained active.

Useful retained test resources (signed-in demo account):

- [Repaired scanned-book reader](https://web-production-8529e.up.railway.app/read/590):
  four pages and four chapters; related cards are under Cards.
- [Disposable digital-book reader](https://web-production-8529e.up.railway.app/read/587)
  and [paper reader](https://web-production-8529e.up.railway.app/read/588).
- Course `7f5a9c0e-cd07-401b-99a6-e39840157ce9`, labelled production verification.
- Uploaded video `de5509b7-fac2-4d10-8d21-498957fb20a3`, ten seconds with supplied captions.
- From Books → Revision sheet → Saved sheets, open Introduction and Overview.
- Adaptive report `85d3c21d-3d9c-40fe-b37c-e766ba66df97`; ideal dialogue
  `6ae896ff-6b41-4c18-a302-6874be58a27e`, both visible under Interview.

[Grafana operations](https://petitecicada3339.grafana.net/d/study-partner-operations)
and [PostHog usage](https://us.posthog.com/project/639444/dashboard/2157665) contain
actual production delivery. LangSmith projects are `agentic-study-partner-production`
and `agentic-study-partner-production-evaluation`. Example grounded-chat root:
`01a0fd8a-f8c8-7152-9bc4-c895e19a66f9`; its correlated Tempo trace is
`6ca0aada30aa73cfa0e3a6614f1bb10d`, with a matching Loki log.

Detailed responses, receipts, prompt/source snapshots and voice proofs are private
in `/tmp/study-production-verification`; bearer tokens expire and must be refreshed
through the normal login flow. This directory and backup must never be committed.
Use [the full verification checklist](verification-checklist.md) for further checks.

## Remaining checks

1. **Your actual microphone/speaker and browser permissions.** The synthetic audio
   transport tests passed, but cannot prove your device setup. Start a new adaptive
   interview on the disposable source, enable the microphone yourself, hear the
   question, dictate a short answer, check the transcript, confirm submission and
   stop. Repeat narration and ideal playback/replay on your device if needed.
2. **Fresh YouTube URL acquisition.** Uploaded and existing videos passed. The hosted
   worker intentionally delegates external downloads; a separate acquisition worker
   and its network/caption access must be verified before claiming fresh URL support.
3. **Device analytics opt-out and wider browser/accessibility coverage.** DNT/opt-out,
   disabled autocapture/replay and redaction have isolated test coverage; actual device
   opt-out and a full browser/assistive-technology matrix were not exercised here.
4. **Broader quality and controlled resilience/load runs.** Only bounded outputs and
   safe failures were tested live. The full gold suite, multi-turn interview quality,
   provider outages, worker-kill recovery and performance under concurrency need
   separately scoped checks; do not inject destructive faults into this production run.


## 3 October follow-up: ideal interview generation

The user reported repeated `Request failed (500)` when starting a complete
chapter ideal interview. Production web logs confirmed that Next's external
rewrite disconnected at its default 30-second proxy timeout. The API continued
and saved the first result after 205 seconds; a second request generated the
same chapter independently and failed the saved-flow unique constraint after
202 seconds. The completed result was retained with all 39 topics covered.
This exposed a gap in the earlier acceptance: long generation was tested via
the API origin, while browser acceptance reopened an already saved flow.

The fix sets the Next rewrite timeout to a finite 10 minutes and serializes
matching ideal generations with a Postgres transaction advisory lock. The lock
key includes owner, canonical scope, format, target level, model and prompt
version, matching the saved-flow uniqueness contract. A waiting retry reads
the committed result before making model calls. Failure rolls back and releases
the lock; unrelated generation settings remain independent. No migration or
source-content change is required. Generation remains synchronous: this fixes
the observed failure, but does not make work durable across a process restart
or provide a resumable job if a chapter takes longer than 10 minutes.

Local regression: `cd frontend && npm run verify:proxy` runs the actual Next
server against a local upstream that delays POST response headers by 35 seconds.
It returned 201 with request body and authorization preserved. This explicit
smoke check makes no paid calls and is separate from the fast Vitest suite.
Real isolated-Postgres regressions cover concurrent identical requests, settings
isolation, cache reuse and failed-generation retry. Twelve targeted tests passed.
Full isolated suite: 2,106 tests and 962 subtests passed, 38 skipped (134.93s).
Frontend: 753 tests passed (28.65s); TypeScript and production build passed.
Fix checkpoint `ba6a887` was committed and pushed. Production deployments:
API/combined worker `62eab578-fc36-4e0b-8ace-40de69abf7c0` and web
`4635f254-db6b-45e5-a9ec-24b75f1d934b`, both SUCCESS. API health reports
`ba6a887`; voice services were not changed.

Two overlapping authenticated demo requests went through
`https://web-production-8529e.up.railway.app/api/ideal-interviews`, not the API
origin, for a previously uncached chapter (book 10000542, node 10005641).
Both returned 201 and the same flow `1385c751-07c8-4f58-be9e-dcbda4fe23c3`
after 264.736s and 262.730s. All 45 topics are represented once with citations;
the responses and persisted GET transcript match. Browser readback displayed
45/45 topics and page grounding. Audio playback was not repeated in this
follow-up; generation/persistence/proxy behavior is the acceptance scope.

Hosted LangSmith readback:

- [Generation tree](https://smith.langchain.com/o/6ca75113-2771-470e-9881-ee62b4c7d1ce/projects/p/46a8e1c0-2129-4a17-9784-8820723cc749/r/01a0fe5f-1490-73f3-a5f8-0a127ee14262?poll=true): 728 spans, 45 generated exchanges, 68 model calls including citation repairs.
- [Concurrent retry tree](https://smith.langchain.com/o/6ca75113-2771-470e-9881-ee62b4c7d1ce/projects/p/46a8e1c0-2129-4a17-9784-8820723cc749/r/01a0fe5f-1c0f-74c2-9239-978a750497ca?poll=true): three spans, zero generated exchanges/model calls, same saved flow.

Both roots ended with 201; all spans ended, no errors or missing parents.
Observed OpenRouter key-usage increase during acceptance was $0.030895495;
the returned generation estimate was $0.019387. These are distinct measures:
key usage may include concurrent activity and delayed receipts, and the app's
estimate is not invoice reconciliation. Cumulative key delta from the original
production-test baseline is $0.259293, below the authorized $5 ceiling.
Private response and trace proofs remain in `/tmp/study-production-trace-diagnosis`
and `/tmp/study-production-verification`, outside Git. This confirms delivery,
coverage/citation contracts and duplicate prevention; no new quality-judge or
human-calibration result is implied.


## 3 October follow-up: verified account identity

Code release `0983a1c` was pushed and deployed from the clean release worktree.
Four Railway deployments are SUCCESS:

| Service | Deployment |
| --- | --- |
| API / combined worker | `7194e5be-0ac5-4923-a7e4-f0e0f4ccbc34` |
| Interview voice | `1d8332f7-9539-4a2d-b282-cd7fde80cd7b` |
| Ideal interview voice | `812bc76f-e7eb-4cf7-bb62-69dd104d903a` |
| Narration voice | `47112fff-f8ce-4a73-b228-9280333c2a29` |

Web-origin health reports `0983a1c`; authenticated book listing returned 200,
invalid-token listing returned 401, and a cited fixture-book chat returned 200
in 10.954s. [The live chat tree](https://smith.langchain.com/o/6ca75113-2771-470e-9881-ee62b4c7d1ce/projects/p/46a8e1c0-2129-4a17-9784-8820723cc749/r/01a0fe9f-7306-7ce1-a5e7-25cecc8c0f9e?poll=true)
contains 23 ended runs, including two LLM calls; every run carries the verified
auth UUID as `user_id`. Tempo trace `685895f38699883fd71b06a77c872904`
contains 11 spans with the same field. The separately queued card job succeeded
with both topics complete; all nine worker runs carry the same identity.
All three LiveKit workers reached ready and accepted their stop/control RPCs.
Their three setup trees and three command trees each contain two ended,
error-free runs with the correct identity. Voice audio generation was not
repeated for this identity-specific check.

Loki account filtering returned 21 records, including five job-correlated
records. Health and invalid-token logs were read back separately and omit
`user_id`; a spoofed `X-User-Id` did not override verified identity or assign an
anonymous identity. Tempo account search returned 20 traces. Loki stream labels
and all 32 queried production operation metric series omit account identifiers.
Email remains in the account registry; PostHog already identifies the same UUID.
Anonymous/system-wide telemetry remains unassigned and historical data is not
backfilled. Filter examples are in [observability.md](observability.md).

Local verification: 2,109 backend tests and 962 subtests passed, 38 skipped
(119.92s). The expanded native LangChain inheritance assertion also passed in
the 11-test operational suite. Private responses, trace/log readbacks and
voice connection credentials remain outside Git in
`/tmp/study-production-verification/identity-*`. This follow-up verifies
identity delivery and isolation; it does not replace the earlier quality or
device acceptance limits.
