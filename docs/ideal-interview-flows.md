# Ideal chapter interview flows

Ideal flows are listen-only, two-person mock interviews generated from one
complete book chapter. They are revision artifacts, not candidate sessions:
the application never evaluates the listener and never opens a microphone.

## Coverage and grounding

The generator reuses the canonical `ScopeInventory` built for deck and live
interview coverage. Every content-bearing node in the selected chapter is
assigned exactly one exchange before a model is called. An exchange is accepted
only when its answer cites every evidence marker from that assigned node. A completed flow is
persisted only if the ordered topic keys exactly equal the full inventory.

This means “complete” is checkable. It does not mean the model can merely claim
that it discussed the chapter, and it does not use retrieval top-k as a proxy
for chapter coverage.

## Dialogue and audio

System-design flows preserve one scenario while moving through requirements,
architecture, deep dives, trade-offs, failure handling, and validation. The
candidate answer is written as spoken reasoning: decisions first, assumptions
made explicit, and mechanisms connected to consequences. Citations remain in
the UI and are never sent to TTS.

The LiveKit worker uses Cartesia Sonic 3.6, two distinct voices, sentence-aligned
transcription timing, explicit between-speaker pauses, and a default speed of
`0.96`. Configure:

```dotenv
INTERVIEW_LIVEKIT_ENABLED=true
LIVEKIT_IDEAL_TTS_MODEL=cartesia/sonic-3.6
LIVEKIT_IDEAL_INTERVIEWER_VOICE=<voice-id>
LIVEKIT_IDEAL_CANDIDATE_VOICE=<voice-id>
LIVEKIT_IDEAL_TTS_SPEED=0.96
LIVEKIT_IDEAL_PRONUNCIATIONS_JSON={"PostgreSQL":"ˈpoʊst|ɡrɛs|ˌkjuː|ˈɛl","RAG":"ˈræɡ"}
```

Run the worker locally with:

```bash
uv run --extra voice python -m interviews.ideal_voice_worker dev
```

The pronunciation map is deliberately reviewed configuration rather than
model-authored phonemes. LiveKit/Cartesia inline IPA syntax is applied only at
the media boundary, so the stored transcript remains clean and searchable.

## Evaluation

Run the LangSmith experiment with:

```bash
uv run python -m scripts.evaluate_ideal_interview_flows --judge
```

The versioned seed in `evaluation/ideal_interview_flow_seed.json` records the
public transcripts used to derive realism expectations without copying their
wording. It is explicitly a transcript-informed synthetic seed until a human
reviews and freezes expected outputs.

The initial evaluators are intentionally split by what code can know:

- exact ordered topic coverage;
- citation presence, complete marker coverage, and locator validity;
- no citation markers in spoken text;
- standalone, reasonably sized interview questions;
- speakable answer length and conversational reasoning signals;
- monotonic system-design phase progression.

These deterministic evaluators remain the release gates. `--judge` adds one
bounded semantic evaluator for interview realism, answer naturalness,
cross-turn coherence, pedagogical memorability, and grounded correctness. The
semantic score complements rather than replaces the hard coverage and citation
gates.
