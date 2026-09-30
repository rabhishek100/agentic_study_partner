# Interface

The Next.js client presents the grounded API: it selects scope, renders source
evidence, submits commands, and plays media. It does not generate study claims.

## Screens

| Route | Purpose |
|---|---|
| `/`, `/papers` | Book/paper library, upload, outline review, conversations |
| `/read/[bookId]` | PDF reader, saved position, anchored questions |
| `/videos`, `/videos/[videoId]`, `/watch/[videoId]` | Lecture library, ingestion state, playback and study |
| `/courses`, `/courses/[courseId]` | Ordered lectures and course-wide questions |
| `/decks`, `/decks/[deckId]` | Card generation, coverage, review, card side chats |
| `/interviews`, `/interviews/[sessionId]` | Setup, adaptive session, grading report |
| `/interviews/ideal/[flowId]` | Listen-only ideal interview |
| `/prompts` | Stored answer-style controls and preview |

Revision sheets, read-aloud, side chats, and the notification center are
integrated surfaces. Feature logic: [study flows](flows.md).

## Interaction rules

- Citations open the source page/timestamp; text-matching misses fall back to
  page focus. Stale anchors are reported.
- Streaming output reconciles with the saved turn. Reload/retry must not
  duplicate conversations or submissions.
- Job screens use durable server state. Long verbatim passages load at segment
  boundaries. Reading/watch sessions retain position and question threads.
- Voice remains optional; editable text and playback controls remain available
  after media failure.

## Accessibility and verification

Keyboard controls and visible focus, WCAG AA contrast, text alternatives to
color, dialog focus restoration, reduced-motion support, labelled recovery
states, and accessible playback are the interface contract.

Shared primitives: [components/ui](../frontend/components/ui).
Tokens: [globals.css](../frontend/app/globals.css).

```bash
cd frontend
npm run lint:tokens
npm run typecheck
npm test
npm run build
```

Browser checks cover keyboard order, narrow layouts, citation navigation,
interrupted streams, media failures, and reduced motion.
