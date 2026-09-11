# Read-aloud player and grounded voice questions

Branch: `codex/read-aloud-voice-agent`.

## Product contract

Read-aloud is one application-level activity. Starting it from a main answer,
side chat, lecture answer, exchange, or selection stops the previous reading
and opens one responsive floating transport. The inline button remains the
short path to start or pause; detailed control lives in the transport.

The player operates on independently synthesized sentences. This makes sentence
highlighting, previous/next, rewind, and seeking deterministic instead of
guessing a timestamp inside a multi-sentence MP3. The first sentence is fetched
immediately and the next two are prefetched. Actual media durations replace
the initial word-count estimates as metadata arrives. Server-side TTS caching
is still keyed by text, model, and voice, so replay does not invoke the provider.

Auto-follow uses a CSS Custom Highlight range over the rendered answer. It does
not mutate stored Markdown or replace the browser's selection. A browser without
Custom Highlight support receives a block-level fallback. Reduced-motion
preferences turn smooth scrolling off. Wheel or touch interaction suppresses
the current automatic scroll, and the next sentence resumes following.

## Voice-question boundary

```text
React floating player -- microphone audio --> LiveKit narration room
        ^                                      |
        | hearing / transcript events          | speech-only worker
        |                                      v
        +--- persisted answer audio <--- PostgreSQL ownership check
        |
        +-- question event --> existing book side-chat stream
                                |
                                +--> existing RAG/LangGraph/LangSmith path
                                +--> cited answer persisted in PostgreSQL
```

Voice questions are deliberately available only when reading a recorded turn
of a book conversation. Voice activity pauses the original playback. Final STT
phrases are accumulated, then a short edit/cancel window appears before the
question is automatically submitted. The page creates a `Read-aloud questions`
side chat anchored to the sentence and reuses it for later interruptions in the
same playback context. The UI therefore retains questions, grounded answers,
and citations even if speech fails.

LiveKit is a transport, not a second agent. The narration worker can:

- transcribe microphone audio;
- report hearing, transcript, playback, and failure events; and
- speak a turn only after loading it from an owner-scoped side chat belonging
  to the room's bound parent conversation.

Its RPC schema rejects arbitrary speech text. It does not receive provider
keys in the browser, persist raw media, record rooms, search the corpus, or
generate answers. The existing side-chat endpoint remains authoritative.

After the voice answer, narration stays paused and offers an explicit **Resume
reading** action. Resuming also restarts listening when the voice toggle remains
enabled. Turning the toggle off disconnects the microphone room.

## Configuration

The feature is disabled by default. It reuses the existing LiveKit Cloud
project and optional Python dependencies while using a separate named worker.

```sh
# API and narration worker
NARRATION_LIVEKIT_ENABLED=true
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
LIVEKIT_NARRATION_STT_MODEL=deepgram/nova-3
LIVEKIT_NARRATION_TTS_MODEL=cartesia/sonic-3
LIVEKIT_NARRATION_TTS_VOICE=...

# frontend build
NEXT_PUBLIC_NARRATION_LIVEKIT_ENABLED=true

uv run --extra voice python -m narration.voice_worker dev
```

When narration-specific model and voice values are absent, the worker falls
back to the interview LiveKit settings. Only the public boolean is exposed to
the frontend. The API flag and credentials remain server-side.

The Docker path is:

```sh
docker compose -f docker-compose.yml -f docker-compose.livekit.yml up --build
```

## Failure and evaluation gates

LiveKit connection, microphone, STT, answer generation, and TTS failures leave
the original reading paused and the text UI usable. A side-chat answer remains
visible even when its audio cannot play. Disconnecting never silently starts a
second microphone transport.

Before default enablement, compare the feature-flagged path with typed side-chat
questions using the same source, questions, device, and network. Record:

- speech-start to playback-pause latency;
- technical-term transcript accuracy and edit rate;
- question-end to first answer-audio p50/p95;
- false interruptions caused by the playing narrator;
- reconnect and recovery counts;
- side-chat citation correctness and unsupported-claim rate; and
- LiveKit STT/TTS usage and cost from worker metrics.

Word-level karaoke highlighting remains out of scope until the chosen TTS path
provides measured word timestamps. Sentence-level alignment is deterministic
with the current provider and avoids inventing timing data.
