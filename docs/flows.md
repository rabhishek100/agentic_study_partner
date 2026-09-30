# Study flows

These flows describe implemented behavior. LangGraph coordinates decision
loops; some generation routines use bounded Python loops. Parsing, scope
loading, scheduling, and persistence remain deterministic.
Source processing: [ingestion](ingestion.md).

## Questions

### Books and papers

```mermaid
flowchart TD
    Q[Question and source scope] --> P[Plan using history]
    P -->|source QA| R[Retrieve evidence]
    R --> A[Draft cited answer]
    A --> S{Supported?}
    S -->|yes| O[Save answer and evidence]
    S -->|no| E[Widen or abstain]
    P -->|other intent| H[Use matching study route]
```

The selection defines which books/papers can be searched. Explicit book
mentions set a turn's search scope without changing the saved selection.
Other routes explain/list/read a complete scope, clarify ambiguity, transform
a previous answer without new facts, or answer externally. History resolves
follow-ups. Quick QA uses 5 chunks; deeper/interview answers use 8. Citations
resolve to source sections/pages, not generated prose.

Code: [study graph](../study/graph.py), [turn execution](../study/conversation.py),
[QA](../study/query.py). Search mechanics: [architecture](architecture.md#retrieval).

### Source widening and external answers

```mermaid
flowchart TD
    A[Anchors and open source] --> S{Sufficient?}
    S -->|yes| G[Source-cited answer]
    S -->|no, more sources| L[Search selected library]
    L -->|sufficient| G
    L -->|insufficient| E{External answers allowed?}
    S -->|no, no more sources| E
    E -->|no| X[Insufficient evidence]
    E -->|yes| M[Model knowledge or web]
    M --> O[Label and save external result]
```

Source-first turns use a finite open-source → selected-library → external
ladder. Each widening and reason is saved. `stay_in_source` stops retrieval
escalation into model/web answers; policy sets the permitted sources. Ordinary chat
can fall directly from failed source QA into external QA without that ladder.

Explicit web requests and recency signals trigger search; otherwise a control
model checks whether general model knowledge is sufficient. Tavily is used
when configured, with DuckDuckGo fallback. Web answers cite returned links;
model-knowledge answers are labelled ungrounded. Neither borrows book citations.

Code: [grounding policy](../study/grounding.py), [external QA](../study/external_qa.py),
[web search](../retrieval/web_search.py).

### Lectures and courses

```mermaid
flowchart TD
    Q[Question and scope] --> P[Plan evidence type]
    P -->|whole lecture or time range| T[Load full transcript scope]
    P -->|evidence question| R[Retrieve mixed evidence]
    R --> S{Sufficient?}
    S -->|no; first attempt| B[Broaden search once]
    B --> R
    S -->|yes or retry exhausted| A[Answer or abstain]
    T --> A
```

Visual questions require visual or supporting-page evidence. Lecture retrieval
widens from 8 to 16 items and a 60- to 180-second window. Course QA searches
published lectures with per-lecture caps: 12/4 initially, 20/6 on retry. Excluded
lectures and source versions are recorded. Prior-answer transforms and
clarification avoid unnecessary retrieval.

Code: [lecture graph](../video/conversation.py), [course graph](../video/course_conversation.py).

## Reading and side chats

### PDF reading mode

```mermaid
flowchart TD
    S[Open or resume reader] --> P[Show PDF]
    P --> A[Ask in source context]
    A --> C[Save source anchors]
    C --> G[Run anchored question]
    G --> R[Answer with page citations]
```

The page composer supplies page context; selections and chapter choices supply
more specific anchors. One reading session per source stores position and
question threads. Citation navigation opens the source location; unresolved
text highlighting falls back to page focus.

Code: [reader](../frontend/app/read/%5BbookId%5D/page.tsx),
[session storage](../storage/conversations.py), [reading API](../api/main.py).

### Anchored side chats

```mermaid
flowchart TD
    S[Select text or evidence] --> A[Save anchor and context]
    A --> R[Resolve source evidence]
    R --> P[Pin source evidence]
    P --> G[Answer with thread history]
    G --> O[Save thread and anchor report]
```

Book pages, passages, sections, lecture moments, and card citations can anchor
a thread. Prior answer text is context; its resolved source evidence is what
can be cited. Missing/stale anchors are reported. Page-anchored book chats can
use the source-first policy; answer-only anchors do not activate that ladder.

Code: [anchor resolution](../study/anchors.py), [context assembly](../study/side_context.py),
[video context](../video/side_context.py), [card context](../decks/conversation.py).

## Complete summaries

```mermaid
flowchart TD
    S[Choose complete scope] --> L[Load complete content]
    L --> B{Fits budget?}
    B -->|no| X[Report scope too large]
    B -->|yes| G[Explain with citations]
    G --> V[Check citations and scope coverage]
    V -->|repair allowed| G
    V -->|citation-safe| O[Save with coverage warnings]
    V -->|unsafe after repairs| F[Reject unverified draft]
```

Chapter explanations load the complete subtree; papers load the complete
paper, and lectures load the selected transcript scope. Top-k retrieval cannot
prove coverage. Book/paper summaries allow up to two repairs for citation or
coverage defects. Coverage checks measure referenced source units, not
claim-level truth. A citation-safe draft can retain coverage warnings after
repairs; unsafe citations fail. Over-budget scopes are not silently truncated.

Code: [scope loading](../study/scope.py), [summary checks and repair](../study/summarize.py),
[lecture scope](../video/lecture.py).

## Verbatim reading

```mermaid
flowchart TD
    S[Choose scope] --> L[Load stored content]
    L --> O[Order page-linked segments]
    O --> I[Serve reading installments]
```

No model generates the passage. Verbatim means reproduction of parsed/OCR
content, not a byte-exact PDF copy. Non-content/decorative blocks are omitted
and counted. Default installments contain about 6,000 characters; oversized
segments remain whole. Excessively large requests are rejected.

Code: [reading](../study/reading.py), [passage API](../api/main.py).

## Flashcards

### Creation

```mermaid
flowchart TD
    S[Choose source scope] --> I[Inventory source topics]
    I --> M{Card mode}
    M -->|generated topics| G[Generate cited cards]
    M -->|printed book questions| E[Extract printed questions]
    E --> A[Ground missing answers]
    G --> V[Validate cards and coverage]
    A --> V
    V -->|one coverage repair| G
    V --> O[Save cards and metrics]
```

Scopes are book chapters, complete papers, or lectures. Generated-card coverage
uses cards that survive validation, not model labels.
Extracted cards retain printed-question identity and answer provenance.
Enabled, eligible sources automatically queue initial sets after publication;
a periodic reconciler recovers missed enqueue hooks without duplicate sets.

Code: [pipeline](../decks/pipeline.py), [generation](../decks/generate.py),
[printed questions](../decks/extraction.py), [automation](../decks/source_preferences.py).

### Review scheduling

```mermaid
flowchart TD
    Q[Due cards before new cards] --> C[Apply daily limits]
    C --> R[Review and rate card]
    R --> S[Update review schedule]
    S --> D[Save event and due time]
    D --> Q
```

Scheduling is deterministic SM-2-style logic with a 10-minute learning/relearning
step, ease floor, and interval cap. Daily counts use the configured timezone.
The card side chat explains cited evidence without changing the schedule.

Code: [scheduler](../decks/scheduler.py), [review persistence](../decks/store.py).

### Daily notifications

```mermaid
flowchart TD
    P[Reminder preferences] --> W[Check reminder time]
    W --> Q[Build daily queue]
    Q --> E{Cards available?}
    E -->|yes| N[Save daily notification]
    E -->|no| S[Schedule next reminder]
    N --> S
    N --> U[In-app or browser alert]
    U --> R[Open daily review]
```

Reminders default to disabled. Locks and a unique daily identity prevent
repeated notifications; missed days do not create a backlog. The notification
center polls every 30 seconds and supports read/dismiss. Browser alerts require
permission and the running app; this is not background push or email delivery.

Code: [reminder worker](../notifications/reminders.py), [notification store](../notifications/store.py),
[notification UI](../frontend/components/notifications/notification-center.tsx).

## Revision sheets

```mermaid
flowchart TD
    S[Complete source and figures] --> I[Inspect and inventory]
    I --> C[Compose sheet]
    C --> V[Validate content and citations]
    V -->|bounded content repair| C
    V --> R[Render and check fit]
    R -->|one fit repair| C
    R --> J[Review quality]
    J -->|at most two quality repairs| C
    J --> P[Publish with findings]
```

No source sampling or top-k retrieval is used. Context overflow, invalid
content, and unrecoverable layout failures fail the job. Unresolved quality
findings after the repair budget remain visible in provenance. Repairs preserve
existing item identities; saved artifacts include PDF/HTML, inventory, figure
inspection, and review history. Follow-up QA reloads the original complete
chapter/paper and validates its citations.

Code: [generation graph](../revision_sheets/generate.py), [rendering](../revision_sheets/render.py),
[sheet QA](../revision_sheets/ask.py).

## Interviews

### Session and question creation

```mermaid
flowchart TD
    S[Choose scope and settings] --> I[Build topic inventory]
    I --> P[Plan topics and phases]
    P --> G[Write question and rubric]
    G --> V[Validate question]
    V --> C[Save question and start clock]
```

Planning is deterministic; models write questions and rubrics. Formats cover
concepts, system design, and source-led practice. Realistic/guided modes control
feedback and assistance. Source coverage grounds assessment while question
wording can frame a practical interview scenario.

Code: [service](../interviews/service.py), [planning](../interviews/planning.py),
[question generation](../interviews/question_generation.py).

### Adaptive interview

```mermaid
flowchart TD
    A[Submit answer] --> E[Grade against rubric]
    E --> W[Verify flagged extensions]
    W --> D[Update checkpoint]
    D --> F{Continue?}
    F -->|no| R[Save report]
    F -->|yes| N[Clarify, probe, or advance]
    N -->|save next question| A
```

Each criterion is scored 1–5: correctness 30%, depth 20%, reasoning 15%,
tradeoffs 15%, clarity 10%, independence 10%. Structured completion/probe flags
and correctness/depth scores determine the route; a single aggregate score does
not choose the next question. A topic normally receives one primary question
and at most one focused follow-up. System-design phases can revisit a topic
for a different design decision. Unverified extensions cannot improve a score.

Clarification requests and optional code/screen observations feed the saved
session. Audio transcription fills an editable draft; only submission advances
the API state. The report records evaluated answers, scores, coverage, and
revision areas. Voice transport: [interview voice](interview-voice.md).

Code: [answer graph](../interviews/graph.py), [score contracts](../interviews/contracts.py),
[evaluation validation](../interviews/evaluation.py).

### Ideal chapter interview

```mermaid
flowchart TD
    S[Chapter inventory] --> P[Plan topic coverage]
    P --> G[Write ideal exchanges]
    G --> V[Check citations and coverage]
    V --> F[Save reusable flow]
    F --> A[Text or two-voice playback]
```

This is a listen-only study artifact, without candidate grading. Each assigned
topic appears once; failed exchanges allow at most two generation attempts.
Reuse is keyed by scope, format, level, model, and prompt version.

Code: [ideal generation](../interviews/ideal_generation.py), [ideal service](../interviews/ideal_service.py).

## Read-aloud

```mermaid
flowchart TD
    T[Saved answer or passage] --> S[Prepare spoken chunks]
    S --> C[Reuse or generate speech]
    C --> P[Play and track passage]
    P -->|optional voice question| V[Pause and dictate question]
    V --> A[Submit anchored question]
    A --> G[Run study graph]
    G --> R[Save and speak answer]
    R --> P
```

The client prepares a narration script from existing content; server APIs
provide speech and source-grounded figure descriptions. Audio is cached by
owner/content/model/voice. Optional LiveKit carries interruptions and saved
side-chat speech; it does not generate answers independently.

Code: [narration script](../frontend/lib/narration.ts), [speech API](../api/narration.py),
[figure descriptions](../narration/figures.py), [voice worker](../narration/voice_worker.py).
