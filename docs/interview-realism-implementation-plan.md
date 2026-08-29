# Interview realism implementation plan

## Goal

Make grounded interviews behave like real technical interviews without copying
employer question banks or turning source headings into candidate-facing trivia.
The selected book or lecture remains the private correctness boundary; the live
session is an independent engineering conversation.

## Quality rubric

The evaluation has three layers. A semantic score can never override a hard
grounding or presentation failure.

| Layer | Measures | Pass condition |
|---|---|---|
| Hard gates | Cited private answer, active-topic grounding, one audible objective, no source/book dependency, no repeats, valid work sample | Every check passes |
| Deterministic session quality | Candidate-led design opening, planned-move alignment, question-shape diversity, duration fit, topic breadth, fallback rate | All quality gates pass; fallback rate reported separately |
| Independent semantic judge | Realism, progression, shared-problem coherence, pacing, role relevance, source independence, useful interview signal | Every dimension is at least 3/4 |

The coherence rule is role-specific. System-design rounds must preserve one
evolving problem. Concept rounds may sample breadth with different compact
scenarios when the sequence remains thematically progressive.

The transcript-derived interaction suite remains pre-split into development and
held-out sources. It measures whether the interviewer clarifies ambiguity,
deepens active work, advances at the right time, stays neutral, and asks a
bounded next question.

## Implementation sequence

### 1. Freeze the rubric and baseline — complete

- Added candidate-led opening and duration-fit gates.
- Added coherence and pacing as independent semantic dimensions.
- Kept grounding, atomicity, source independence, and non-repetition as hard
  failures.
- Made semantic-judge failures inspectable instead of aborting a complete run.
- Added judge-only replay over saved generations so generation is never repeated
  merely because a judge provider failed.

### 2. Preserve one system-design problem — complete

- The initial turn opens a product capability and invites requirement
  clarification before architecture.
- Every later primary system-design question receives the preceding question and
  candidate answer as authoring context.
- Prompt policy explicitly requires later turns to continue the same scenario.
- Compact sources may reuse grounded evidence for a later design phase rather
  than ending after one source node.

### 3. Make pacing phase-aware — complete

- A 15-minute design round has a three-phase floor.
- A 30-minute design round has a five-phase floor: requirements, architecture,
  trade-off, diagnosis, and validation.
- Extra phases are asked only when deterministic source signals support them;
  unsupported phases are never invented to fill time.
- Large scopes no longer repeat two near-identical requirements questions at the
  opening.

### 4. Keep the live experience neutral — complete

- Realistic-mode reactions rotate through neutral variants without correctness,
  score, or model-answer leakage.
- Live realistic API responses hide source-derived question topic labels and
  checkpoint labels until the interview is complete.
- Candidate-specific clarification probes survive provider fallback and remain
  atomic.

### 5. Make outage fallback interview-like — complete

- Fallback system-design questions no longer expose solution-bearing source
  headings such as queue, idempotency, or a named failure.
- The fallback arc asks the candidate to own the request flow, next production
  trade-off, diagnostic priority, and launch signal.
- The fallback remains cited and deterministic while sounding like an evolving
  interview rather than a source checklist.

### 6. Calibrate scoring with human-written candidates — complete

- Froze weak, mixed, and strong answers before grading for a proximity-service
  requirements question and a RAG-system requirements question.
- The evaluator uses the production prompt, structured `AnswerEvaluation`
  contract, citation sanitation, score weights, and route flags. It deliberately
  does not use a candidate model or question-generation model.
- Gates use broad predeclared score bands and allowed route sets. Each complete
  scenario also requires `weak < mixed <= strong`; no exact model score is
  encoded as the target.
- Provider errors are counted separately and never converted into candidate or
  rubric failures.

## Measured results

- Focused unit and evaluation tests: **105 passed**.
- Transcript-derived interaction run after the policy change: **8/8 hard
  candidate-experience passes**. Hosted question generation was unavailable in
  that run, so generation robustness was **0/8** and is reported separately.
- Five-profile sequence generation: **5/5 hard passes** and **5/5 deterministic
  quality passes**. Provider timeouts reduced authored-generation robustness to
  **2/5** in that stochastic run.
- Senior ML-system sequence: independent judge scored **4/4 on all seven
  dimensions**.
- Senior SWE-system sequence before the open-ended fallback change: passed, but
  realism and interview signal were **3/4** because prompts exposed specific
  architecture topics.
- Final provider-outage SWE-system sequence after the hill climb: **4/4 on all
  seven semantic dimensions**, with no violations. Its five questions progress
  from candidate-led clarification to request flow, an open production trade-off,
  a degraded-component diagnosis, and a launch signal.
- Live human-written proximity profiles passed **3/3** calibration gates and
  scored **1.90 → 3.95 → 5.00**. Routing progressed from `continue`, to one
  warranted `depth_follow_up`, to `advance`.
- The live RAG weak answer passed at **1.90** and routed to `continue`. The RAG
  mixed and strong calls failed at the provider boundary, so no score or rubric
  verdict is claimed for them. The run records **4/6 completed profile passes,
  two provider failures**, and one complete monotonic ladder.
- A bounded retry of senior SWE and ML-system question generation produced
  **2/2 hard and deterministic quality passes**, but **0/2 authored-generation
  robustness passes**: all ten hosted calls failed and safe grounded fallbacks
  supplied the candidate-visible sequences.

## Remaining validation

- Re-run the two incomplete RAG profiles when provider capacity is available.
  Do not tune thresholds or answers based on the missing outcomes.
- Repeat hosted generation when provider capacity is stable and measure fallback
  rate across multiple seeds. The deterministic candidate experience is strong,
  but this run does not support a claim of reliable authored generation.
- Have a human reviewer score complete transcripts for conversational naturalness,
  difficulty calibration, and whether the interviewer responds to actual design
  choices rather than merely following phase labels.
