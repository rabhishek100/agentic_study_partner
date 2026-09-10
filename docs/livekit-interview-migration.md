# LiveKit interview voice migration

Branch: `codex/livekit-interview-voice`.

## Objective and boundary

Replace the interview's recorded HTTP clips and downloaded MP3 playback with
WebRTC media and streaming speech services. Keep the grounded interview service,
LangGraph evaluation, source inventory, PostgreSQL checkpoint, report, coding
workspace, and explicit answer submission authoritative.

The observed failure is that capture currently waits for transcription before
recording another segment. The other target is the sequential wait for fully
buffered reaction and question audio. LiveKit does not remove evaluation or
question-generation latency.

```text
React -- authenticated token request --> FastAPI -- owner-scoped session lookup
React <========= WebRTC =========> LiveKit room <====> Python voice worker
  |                                                    | streaming STT/TTS
  |<---------------- draft transcripts ----------------|
  |--- speech command (saved turn, not arbitrary text) ->| PostgreSQL lookup
  |
  +-- explicit Send --> existing answer API --> LangGraph --> PostgreSQL
```

## Implementation sequence

1. Add optional voice dependencies and disabled-by-default configuration.
   Mint short-lived microphone-only participant tokens after ownership checks;
   explicitly dispatch a named worker with server-authored session metadata.
2. Add a speech-only worker: continuous transcription, explicit capture epochs,
   bounded RPC commands, persisted question/reaction/clarification lookup, and
   streaming TTS. It must never generate or score an answer independently.
3. Adapt the React voice and speech interfaces behind a build-time feature flag.
   Preserve editable transcripts, microphone choice, manual Send, push-to-talk,
   reaction-before-question sequencing, pause, replay, and text operation when
   the media connection fails. Cancel obsolete playback and reject stale text.
4. Bind answer requests to the expected turn so a retry cannot answer a newer
   question. Keep existing transaction protection and response reconciliation.
5. Test authorization, utterance privacy, stale messages, cancellation, reconnect
   behavior, and existing interview behavior. Document local worker setup.
6. With configured LiveKit credentials, run paired live microphone sessions and
   record results before enabling the feature by default.

## Acceptance checks

- No words dropped when speaking while a previous transcript is arriving.
- Thinking pauses append to the draft and never submit it.
- Only persisted candidate-facing utterances are spoken; private rubrics and
  future questions cannot be requested through the voice worker.
- A stopped or superseded request cannot begin playing later.
- Old capture epochs cannot append text to a new answer or clarification.
- Duplicate/stale answer requests cannot settle a different turn.
- Pause, disconnect, refresh, and provider failures leave a usable text flow.
- No raw media is persisted by application code; do not enable room recording.
- Existing interview tests and frontend type checks pass with the flag off.

## Evaluation gate

Use the same source, candidate script, device, and network for both transports.
Include long answers, thinking pauses, technical terms, background noise, mic
changes, interrupted playback, reconnects, and a final answer/report transition.
Report dropped words, transcript accuracy, submission-to-first-audio p50/p95,
reaction-to-question gap, failure/recovery counts, and per-session provider and
LiveKit usage. Keep LangSmith tracing for the existing reasoning graph. Treat
unmeasured media cost as unknown, never as zero. Do not claim improved latency
until these live checks have been run.

## References

- https://docs.livekit.io/agents/logic/turns/
- https://docs.livekit.io/agents/multimodality/audio/
- https://docs.livekit.io/agents/server/agent-dispatch/

## Status

The first implementation covers steps 1–4, plus automated boundary/lifecycle
tests and local/Docker configuration. Live provider evaluation remains pending:
there are no LiveKit credentials in the local environment. The legacy transport
remains the default. LiveKit Cloud was selected for the first evaluation.

## Run the experimental path locally

1. In the root `.env`, set `INTERVIEW_LIVEKIT_ENABLED=true`, `LIVEKIT_URL`,
   `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` from your LiveKit Cloud project.
   Use the same project on the API and voice worker. Enable Cloud Inference and
   select a voice ID compatible with `LIVEKIT_INTERVIEW_TTS_MODEL`; put that ID
   in `LIVEKIT_INTERVIEW_TTS_VOICE`. The initial configurable models are
   `deepgram/nova-3` and `cartesia/sonic-3`; these are trial choices, not measured
   winners. No new OpenAI key is needed for this media path.
2. Install optional Python dependencies with `uv sync --extra voice` and frontend
   dependencies with `npm ci` from `frontend/`. Preserve the root `.env` database
   and existing reasoning-provider configuration.
3. Start the API with its normal documented command, then start a separate
   process from the repository root:

   ```sh
   uv run --extra voice python -m interviews.voice_worker dev
   ```

4. Set `NEXT_PUBLIC_INTERVIEW_LIVEKIT_ENABLED=true` in `frontend/.env.local`,
   then restart the frontend development server (or rebuild its production
   bundle). Only this boolean is public; LiveKit credentials stay server-side.
5. Start an interview normally. The current question is spoken through the room;
   after playback, streaming transcription appends finalized phrases to the
   editable answer. Use **Transcribe now** to flush the latest phrase before
   submitting. **Send answer** submits the visible text only, just as before.
   Sending discards late text from that capture epoch.

For Docker, the optional overlay builds the API with the voice extra, enables
the frontend flag, and starts a separate voice worker:

```sh
docker compose -f docker-compose.yml -f docker-compose.livekit.yml up --build
```

The root `.env` must contain the Cloud configuration above. Docker still uses
the existing database connection convention. No LiveKit server is started by
this overlay; media connects to Cloud.

## Recovery and rollback

The first version keeps half-duplex interview behavior: microphone input pauses
during speech, and explicit Stop cancels playout. Automatic barge-in and automatic
answer submission are intentionally deferred. A connection interruption leaves
the draft editable and requires an explicit restart of listening; it cannot
submit or regenerate a question. Replay reads the saved current question.

A media failure shows an error and keeps typed answers usable. It does not
silently open a second microphone transport or synthesize another paid copy.
For rollback, set the frontend flag to false and rebuild/restart; disable the
backend flag and stop the voice worker. Existing interviews and reports remain
compatible because the authoritative answer/state schema is unchanged apart
from an optional expected-turn field on answer requests.

## Usage and current limitations

The worker emits structured `interview_voice_usage` logs with the session ID,
room name, and SDK-provided STT/TTS metrics. It does not store audio, transcripts
from the media stream, or screenshots. Only explicitly submitted answers reach
the existing canonical interview store. SDK room recording is disabled.

LiveKit media/inference charges are added to the stored interview total and a
separate `voice_cost_usd` subtotal. Rates are configurable because provider
pricing changes; the defaults are the current Build/Ship prices for Deepgram
Nova-3 monolingual and Cartesia Sonic 3. Reconcile Cloud usage with structured
worker logs during the pilot. Existing LangSmith reasoning traces remain unchanged.
The source for those defaults is LiveKit's public inference pricing page:
<https://livekit.com/pricing/inference>.
The pilot must validate playback timing, STT finalization, device switching,
autoplay unlocking, reconnect behavior, and cost before default enablement.

## Automated validation (2026-09-09)

- 87 backend tests passed across the interview, voice boundary, and worker tests.
- 31 frontend tests passed across interview setup/activity, legacy speech, and
  LiveKit event/lifecycle behavior.
- TypeScript validation passed. Next.js production builds passed both with the
  existing configuration and with the LiveKit feature flag enabled.
- The combined Docker Compose configuration and the frozen Python lock resolve.
  API and voice Docker images subsequently built successfully on Railway.
- `npm audit --omit=dev` fails on existing versions of Next.js (16.3.0), sharp
  (0.35.3), and baseline-browser-mapping (2.10.44). All three versions match the
  base branch. Dependency remediation is separate from this media migration.
- At initial validation, no Cloud room or paid inference session had been
  started. Subsequent production observations are recorded below.

## Production pilot (2026-09-10)

The user requested a direct production test. Railway production now has a
separate `voice` service running `python -m interviews.voice_worker start`.
It uses the API service's LiveKit credentials and database through Railway
variable references. Its HTTP health endpoint is `/` on port 8081; idle job
processes are disabled, and each job starts on demand. The API voice flag is
enabled. Credentials remain server-side.

Verified deployment `ef5dfdbd-5d48-4633-acc7-5406118adba2` for the API and
`32771a2a-caee-4018-b862-ce961b2f91e6` for voice. The API reports both databases
ready at `/api/health`; unauthenticated voice-token requests return 401.
The voice process registered as `interview-voice` with LiveKit Cloud, its health
endpoint returned 200, and a database `SELECT 1` succeeded from its container.
The deployed source revision is `51b8e5d-dirty` from this feature branch.

The production web pilot uses `NEXT_PUBLIC_INTERVIEW_LIVEKIT_ENABLED=true`.
Deployment `d914f03e-2142-4e27-a004-d6a1595defc1` succeeded, including its
Railway health check; the public website returned HTTP 200.
This requires a frontend rebuild through `scripts/deploy.sh web production`.
After rollout, refresh the site and start or resume an interview to test
question playback, streaming transcription, and explicit answer submission.
Real microphone behavior and inference quality still require this live test;
worker registration and health checks do not establish those properties.

For rollback, set the web flag to `false` with `--skip-deploys`, then run
`scripts/deploy.sh web production`. Prior successful deployments are API
`8073687d-ec62-4dfe-9fd9-715f9edbbb84` and web
`939b4cc1-706b-4858-8466-e4412767ad91`. Disable the API voice flag and stop the
voice service after clients have returned to the legacy transport.

### Pilot observations and follow-up

The user's production smoke test generated four TTS metric events (564
characters) and fourteen STT metric events (37.4 seconds of audio) in the
inspected 250-line worker log window. Median provider TTS time to first byte
was 0.205 seconds across those four events. This is provider latency, not
browser playback latency or evidence of an improvement over the old transport.
At the current defaults, that observed voice usage is approximately $0.0312.
It predates the cost migration, so the historical report retains its original
model-only total rather than being rewritten from a partial log window.

Disconnect exposed a cleanup exception: LiveKit had already closed AgentSession
before the job shutdown callback called `interrupt`. Cleanup now delegates
session shutdown to its idempotent `aclose` and still closes both providers.
A regression test reproduces the closed-session condition. Additional browser
hook tests cover explicit restart after reconnect, stale transcript rejection,
and leaving an interview while its token request is pending. The backend
interview/voice suite now passes 88 tests; frontend type checking passes.

The production smoke test completed an interview through its final cited report.
Physical microphone switching and a paired legacy/LiveKit benchmark remain useful
follow-up evaluation rather than release gates. Provider usage is logged and new
usage is priced into the session total. Sessions completed before the cost
migration retain their historical model-only totals.

### CI release fixes (2026-09-10)

Production dependency auditing now passes after updating the lockfile to Next.js
16.3.4, sharp 0.35.4, and baseline-browser-mapping 2.11.21. All 703 frontend
tests and the LiveKit-enabled production build passed with these updates.
The Python CI failure consisted of eight PDF-rendering tests whose Chromium
executable was missing; CI now installs Chromium and its system dependencies
before running those tests. These supersede the initial audit findings above.
