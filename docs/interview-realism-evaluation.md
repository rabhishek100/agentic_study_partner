# Interview realism evaluation

## Decision

The interview feature treats a selected chapter or lecture as a private
grounding and scoring boundary, not as the language of the interview. A
candidate should hear a self-contained technical problem that could appear in
a real engineering interview. They should not need to know that a book exists,
remember a heading, or reproduce an author's taxonomy.

The session now plans an observable interview arc in addition to topic
coverage. The arc is a preference rather than permission to invent unsupported
material:

| Format and level | First-pass progression |
|---|---|
| Concept, entry | fundamentals → mechanism → application → diagnosis |
| Concept, mid | fundamentals → application → diagnosis → trade-off |
| Concept, senior | application → architecture → trade-off → diagnosis |
| System design or source-led | requirements → architecture → trade-off → diagnosis → evaluation |

Follow-ups remain adaptive. A clarification targets ambiguity in the answer.
A later revisit targets a recorded gap or an explicitly warranted depth area.
The progression table applies only to first-pass breadth and never forces an
unrelated question onto evidence that cannot support it.

## Research basis

This is a synthesis of public, first-party guidance and an openly authored ML
interview resource. It deliberately does not copy leaked or proprietary
question banks.

- [OpenAI's interview guide](https://openai.com/interview-guide/) says the
  process evaluates how candidates approach problems, make decisions, and
  communicate reasoning. Engineering assessment may include pair coding,
  technical tests, or take-home work, with attention to design quality, code
  quality, performance, and test coverage.
- [Meta's Software Engineer Full Loop Interview Guide](https://d3no4ktch0fdq4.cloudfront.net/public/course/files/Meta_SWE_full_loop_guide.pdf)
  describes coding as a conversation that measures communication, problem
  solving, executable code, and verification. Its design round moves from
  requirements and a high-level design into trade-offs, risks, failure points,
  and technical details.
- [Amazon's SDE II preparation guide](https://amazon.jobs/content/en/how-we-hire/sde-ii-interview-prep)
  describes an interactive system-design discussion in which the interviewer
  probes a design and the candidate asks questions to validate it. It names
  practicality, accuracy, efficiency, reliability, optimization, and
  scalability as design objectives, and expects robust tested code with edge
  cases.
- [Microsoft's technical interview guidance](https://news.microsoft.com/life/how-to-ace-a-technical-interview-at-microsoft/)
  emphasizes clarifying assumptions, narrating reasoning, diagramming when
  useful, checking work, adapting to changed requirements, and making the
  interview a conversation rather than a question-and-answer test.
- [Machine Learning Interviews](https://github.com/chiphuyen/ml-interviews-book)
  is based on the author's experience as both an ML candidate and interviewer.
  It documents loops that mix ML theory, coding or implementation, system
  design, and behavioral assessment; it also notes that open-ended ML
  system-design questions are especially relevant for senior roles.
- The [OpenAI o1 System Card](https://cdn.openai.com/o1-system-card-20241205.pdf)
  describes a real research-engineer interview evaluation containing both ML
  knowledge questions and self-contained coding problems with unit tests in the
  task contract.

These sources vary by company and role. The shared evidence supports the
general behavior above; it does not justify claiming that every company uses
the same loop or that passing this simulation predicts a hiring result.

## Transcript-derived interaction corpus

The second layer uses caption transcripts from eight public YouTube mock
interviews and walkthroughs. It stores only video metadata, timestamp ranges,
and paraphrased behavioral observations; full captions are not redistributed.
The corpus spans five channels and includes SWE coding, ML coding, AI coding,
software system design, and ML system design.

Development sources:

- [freeCodeCamp full SWE mock](https://www.youtube.com/watch?v=1qw5ITr3k9E);
- [Exponent K-means coding mock](https://www.youtube.com/watch?v=gl96uVKroDM);
- [Exponent rate-limiter design mock](https://www.youtube.com/watch?v=SgWb6tWx3S8);
- [Think in Models AI engineer mock](https://www.youtube.com/watch?v=O-kuHMOdjX4).

Held-out sources, assigned before the runtime change:

- [NeetCode Google-style coding mock](https://www.youtube.com/watch?v=46dZH7LDbf8);
- [Exponent AI coding mock](https://www.youtube.com/watch?v=ZE_YEn-okfk);
- [Hello Interview video-system design](https://www.youtube.com/watch?v=IUrQ5_g3XKs);
- [MLEpath ML design mock](https://www.youtube.com/watch?v=9U48NlbOzCU).

Across these transcripts, candidates restate or clarify the contract, develop
one shared solution, and then encounter interviewer probes about an assumption,
boundary case, test, changed requirement, failure mode, metric, or deployment
decision. Interviewers usually acknowledge briefly and keep evaluation private.
That evidence motivated two bounded rules: keep ordinary verbal concept rounds
breadth-first, but allow one immediate depth probe for code, architecture, and
system-design work; and keep realistic-mode reactions neutral about correctness.

Auto-captions are noisy and the videos are educational mocks, not confidential
employer recordings. They support interaction-shape claims, not claims about
exact employer questions, scoring validity, or universal interview practice.

## What the eval measures

`evaluation/interview_realism_seed.json` contains five synthetic, role-varied
sessions:

- mid-level machine learning engineering;
- mid-level AI engineering;
- entry-level software engineering;
- senior ML system design;
- senior software system design.

The stimuli are compact source passages written for evaluation. They are not
employer questions and production code contains no case-specific exceptions.

Deterministic hard gates check:

- one focused objective per turn, while allowing one short scenario preamble;
- a cited private answer grounded in the active topic;
- no candidate-facing book, chapter, lecture, author, section, or source
  dependency;
- no repeated question or repeated template with topic words swapped;
- breadth before revisiting a topic;
- no consecutive screen or coding work samples.

Sequence-quality checks measure planned-move alignment, practical-question
rate, and question-shape diversity. Fallback rate is reported as a separate
generation-robustness measure: the target is no more than one fallback per four
questions. The candidate-experience result still judges the final question,
because a safe deterministic replacement is part of the production behavior;
it does not hide that the authored generation path was fragile.

An optional independent judge scores realism, progression, role relevance,
source independence, and useful interview signal from 0–4. When enabled, every
dimension must score at least 3. The judge is diagnostic evidence, not a safety
boundary; deterministic source and presentation failures remain hard failures.

## Running it

```bash
# One inexpensive development case.
uv run python -m scripts.evaluate_interview_realism \
  --case real-ml-mid \
  --judge

# The complete role and level slice.
uv run python -m scripts.evaluate_interview_realism \
  --all \
  --judge
```

Results are written under `evaluation/runs/interview-realism/`. Generated
questions, private rubrics, deterministic failures, model costs, and semantic
judge explanations remain inspectable in the JSON artifact.

When only deterministic classifiers or thresholds change, re-score saved
generations without making new hosted calls:

```bash
uv run python -m scripts.rescore_interview_realism \
  evaluation/runs/interview-realism/<run>.json
```

Run the transcript-derived interaction slice separately. Tune only on
`development`; open `held_out` after the policy is frozen.

```bash
uv run python -m scripts.evaluate_interview_interactions \
  --split development \
  --judge

uv run python -m scripts.evaluate_interview_interactions \
  --split held_out \
  --judge
```

`evaluation/interview_transcript_eval.json` is the inspectable corpus and
synthetic interaction set. Each simulation injects a frozen candidate condition
into the production answer graph, then evaluates routing, question kind,
grounding, source independence, non-repetition, reaction neutrality, fallback
use, and semantic continuation quality.

## Development baseline — 2026-08-25

The complete five-case run produced 22 questions and cost about $0.028 in
reported generation charges. After deterministic replay with the final
classifier:

- candidate-experience pass rate: 5/5;
- hard grounding and presentation pass rate: 5/5;
- semantic means: 3.8/4 realism and 4.0/4 for progression, role relevance,
  source independence, and interview signal;
- authored-generation robustness: 4/5 profiles.

The senior software-system-design profile required deterministic replacements
for three of five questions after generated drafts violated work-sample,
atomicity, or citation rules. The final visible sequence passed the experience
rubric, but this fallback concentration is a real reliability limitation. Do
not describe the authored generator as fully robust until repeated runs lower
that rate.

## Transcript hill climb — 2026-08-26

The pre-change development baseline passed 1/4 interactions. It incorrectly
left the active problem on both depth cases, leaked correctness through live
reactions, and used one fallback. Semantic neutrality averaged 1.5/4.

After the bounded routing and reaction changes:

- development: 4/4 hard and semantic passes, no fallbacks;
- held-out: 4/4 hard and semantic passes, no fallbacks;
- semantic means on both splits: 4.0/4 for natural continuation, probing
  quality, neutrality, role relevance, and boundedness;
- reported generation cost: about $0.001 on the final development run and
  $0.004 on the held-out run.

The original five-profile question-sequence suite still passed all hard and
candidate-experience gates after this policy change. Authored-generation
robustness passed 3/5 profiles in that stochastic regression run; safe
fallbacks were concentrated in entry SWE and senior ML system design. The
candidate-visible sequences passed, but this remains a generator reliability
limitation rather than something the transcript score should hide.

Artifacts:

- `evaluation/runs/interview-interactions/baseline-development.json`;
- `evaluation/runs/interview-interactions/final-development-v2.json`;
- `evaluation/runs/interview-interactions/final-held-out.json`.

This is one stochastic generation pass over eight synthetic interactions. It
is evidence of generalization across the frozen slice, not a confidence
interval or proof that every live session will be realistic.

## Claim boundary

This eval can support the claim that sampled sessions resemble the public
structure and behavior of technical interviews. It cannot establish hiring
validity, interviewer consistency, or candidate outcome correlation. Before a
portfolio release claim, a human reviewer should read every generated sequence
and record whether the questions are natural, appropriately difficult, and
useful for the named role.

## Session-coherence hill climb — 2026-08-26

The next evaluation pass added two session-level dimensions that the original
question and interaction suites did not measure directly: coherence and pacing.
System-design sessions must now preserve one evolving problem, open by letting
the candidate establish requirements, and cover a duration-appropriate phase
arc. Concept interviews retain breadth-first behavior and are judged for thematic
progression rather than being forced into one fictional system.

Implementation and measured results are recorded in
[`interview-realism-implementation-plan.md`](interview-realism-implementation-plan.md).
The main result is deliberately split:

- candidate-experience hard and deterministic quality gates passed across all
  five role profiles;
- the final offline/provider-outage senior SWE design sequence scored 4/4 on
  realism, progression, coherence, pacing, role relevance, source independence,
  and interview signal;
- hosted generation robustness remained poor during the run because question
  calls timed out. Safe fallbacks preserved the interview, but this is not
  evidence that authored generation is reliable.

The semantic rubric itself was hill-climbed once: its first version incorrectly
required one shared fictional problem from breadth-oriented concept rounds. The
final role-specific rule requires a shared problem only for system design and
active work samples; a concept round may use different compact scenarios when
the sequence remains coherent for the named role.

## Candidate-grader calibration — 2026-08-26

`evaluation/interview_candidate_profiles.json` adds two frozen, human-written
answer ladders. One uses the uploaded proximity-service chapter and one uses the
uploaded RAG chapter. Each has a weak, mixed, and strong answer authored before
the run. This avoids the invalid earlier approach of asking a source-assisted
candidate model to grade answers it could make nearly perfect.

The runner exercises the production evaluation prompt, structured answer
contract, weighted scoring, citation sanitation, and adaptive route flags. It
isolates grading from next-question generation so a provider failure in a
second model call cannot alter the candidate score. The predeclared rubric is
intentionally loose:

- weak answers must remain at or below 3.25, remain incomplete, and either
  receive one clarification or continue on the same question;
- mixed answers must score at least 3.5, complete the audible question, and
  either receive one essential depth probe or advance;
- strong answers must score at least 4.0, complete the question, and deepen or
  advance;
- a complete scenario must satisfy `weak < mixed <= strong`.

The live proximity ladder passed all gates at **1.90, 3.95, and 5.00**. The
weak RAG answer also passed at **1.90**. The two later RAG requests failed at
the provider boundary, leaving the aggregate at four completed passes and two
provider failures. Those failures are not candidate failures, and the missing
scores must not be filled from the deterministic test double.

Run or resume selected profiles with:

```bash
uv run python -m scripts.evaluate_interview_candidate_calibration

uv run python -m scripts.evaluate_interview_candidate_calibration \
  --case rag-mixed \
  --case rag-strong
```

The first live artifact is
`evaluation/runs/interview-candidate-calibration/live-human-profiles.json`.
The bounded senior-system generation retry is
`evaluation/runs/interview-realism/live-authored-generation-retry.json`. It
passed both candidate-experience sequences but used fallback for every hosted
question, so authored-generation robustness remains an open failure.
