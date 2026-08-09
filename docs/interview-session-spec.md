# Adaptive interview sessions

## Product contract

An interview session is a source-grounded, voice-first simulation over exactly
one book chapter or one processed lecture. It is not a free-form chat and it
does not widen to a whole book or playlist.

The setup asks for:

- source: one chapter or one lecture;
- maximum duration: 15, 30, 45, 60, 90, or 120 minutes;
- target level: entry, mid, or senior;
- feedback mode: realistic (default) or guided;
- interview format: automatically detected, with a manual concept,
  system-design, or source-led override;
- voice and turn-taking preferences.

The duration is a ceiling, not a quota. The deterministic source inventory is
the coverage contract. A session ends early when it has exhausted meaningful
required topics and useful adaptive follow-ups. It never pads a session merely
to fill the selected time.

## Evidence policy

The selected source is the primary rubric. Candidate answers are classified as
source-aligned, correct extensions, partially correct, incorrect, or
insufficient. Model knowledge may identify a plausible extension, but live web
evidence is fetched only when the extension would materially change the score.
Valid extensions are acknowledged and labelled before the interviewer returns
to the selected source.

Important claims in feedback and suggested answers carry a book page or lecture
timestamp citation. External claims carry web-source provenance. If neither is
available, the system says that the claim was not verified and does not use it
to lower the candidate's correctness score.

## Interview behavior

The source is classified before the interview:

- concept: breadth, intuition, mechanics, edge cases, and application;
- system design: requirements, constraints, estimation, architecture,
  trade-offs, and failure modes;
- source-led: the order already used by a source that presents an interview.

The deterministic topic inventory establishes coverage before any generation
call. Luna writes one question or follow-up at a time. The first two answers
calibrate within the selected target level. Two strong answers increase depth;
a weak answer receives one clarifying probe, then at most two progressively
more explicit hints. Hints reduce independence, not correctness.

Realistic mode gives only natural acknowledgements and probes during the
session. Guided mode adds concise correction after each turn. Exact questions
and topic order stay hidden in realistic mode; guided mode may show coverage
progress without revealing future questions.

## Timing

The planner reserves approximately 10% of the maximum for calibration, 75% for
adaptive questioning, and 15% for synthesis and closing. It stops introducing
new questions at the deadline, lets the current answer finish for at most two
minutes, and then produces the report. Explicit pause stops the clock. Refresh
or connection loss pauses a live session; an intentional early finish still
produces a report.

## Voice and screen sharing

The existing OpenRouter Whisper path transcribes candidate speech. Listening
starts automatically and remains active through the answer. Roughly 1.2
seconds of silence closes only the current audio segment and appends its
transcript to an editable draft; it never submits the answer. The candidate
may pause to think, continue speaking across as many segments as needed, and
explicitly sends the completed draft. Push-to-talk remains available in noisy
or unsupported environments. Candidate speech interrupts TTS. Every new
interviewer question is spoken automatically, with a one-click replay/unlock
fallback where browser autoplay policy requires a gesture.

Kokoro 82M is the default TTS model, selected for modern voice quality at
low per-character cost. Model and voice remain environment-configurable. A
short provider deadline prevents speech generation from blocking the turn;
when hosted TTS is slow or unavailable, the already-unlocked device voice
speaks the question automatically at no provider cost.

Screen sharing is checkpoint-based. The browser shows a local live preview,
but uploads no continuous video. The candidate explicitly submits a still when
code, a diagram, or a whiteboard is ready. Luna returns a structured observation
for the active question; the raw frame is discarded immediately.

Raw microphone audio and screen images are never persisted. Session state,
transcripts, source citations, structured screen observations, scores, and
costs are persisted after every settled turn so an interrupted interview can
resume.

## Evaluation and report

Each settled answer records 1-5 scores for technical correctness, depth,
reasoning structure, trade-off awareness, communication clarity, and
independence. The final score is a deterministic weighted aggregation of those
turn scores. The product does not invent percentiles or predict hiring outcomes.

The final report contains:

- session timing, configuration, and cost;
- question-and-answer transcript;
- per-question scores, classification, citations, hints, and screen feedback;
- strengths and recurring gaps;
- cited recommended answers for weak or incomplete turns;
- valid external additions and their provenance;
- missed source topics and a focused revision plan;
- an evidence-confidence note.

## Architecture boundary

The API owns session state and timing. Postgres stores canonical session events
and a derived resume checkpoint. A small LangGraph turn workflow performs:

```text
load session and active topic
  -> evaluate candidate answer against source evidence
  -> verify a material extension when necessary
  -> update scores, coverage, hints, and difficulty
  -> finish or generate exactly one next question
  -> persist the settled turn and checkpoint
```

The React client owns media devices, voice activity, TTS playback, and the local
screen preview. It generates nothing and never decides scores or coverage.
