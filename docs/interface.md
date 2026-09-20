# Interface

The Next.js client is a presentation and interaction layer over the grounded
API. It may select scopes, render evidence, play media, and submit commands; it
must not create unsupported answers or duplicate server policy.

## Current screens

| Route | Purpose |
|---|---|
| `/` | Book library, uploads, and main conversations |
| `/papers` | Paper library and whole-paper study |
| `/read/[bookId]` | Paginated source reader, citations, side chats, read-aloud |
| `/videos`, `/videos/[videoId]` | Lecture library and ingestion state |
| `/watch/[videoId]` | Video playback, transcript/evidence, grounded chat |
| `/courses`, `/courses/[courseId]` | Ordered lecture courses and cross-lecture chat |
| `/decks`, `/decks/[deckId]` | Flashcard review, coverage, and card side chats |
| `/interviews`, `/interviews/[sessionId]` | Adaptive interview setup, session, and report |
| `/interviews/ideal/[flowId]` | Listen-only generated chapter interview |
| `/prompts` | Stored answer-style controls and preview |

## Interaction contract

- Citations are interactive evidence, not decorative footnotes. Selecting one
  opens the exact page/timestamp when possible and an honest page-level fallback
  otherwise.
- Long chapter/paper text is fetched in server-defined installments at segment
  boundaries, not loaded as one large mobile layout.
- Side chats keep their selected passage/evidence as explicit context and
  report anchors that could not be resolved after a source version changed.
- Streaming answers reconcile with the saved server turn; reloads must not
  create a second conversation or lose a finished response.
- Job screens show durable server state and recover after navigation/reload.
- Voice is optional. Text submission and playback controls remain usable when
  microphone, speech, or LiveKit services are unavailable.

## Accessibility

- All controls must be keyboard operable and have visible focus.
- Text and interactive states must meet WCAG AA contrast.
- Color cannot be the only carrier of state.
- Dialogs trap focus and restore it on close.
- Motion respects `prefers-reduced-motion`.
- Playback exposes play/pause, seek, speed, time, and transcript alternatives.
- Loading, error, empty, disabled, and recovery states require explicit text.

Semantic color/spacing tokens are defined in `frontend/app/globals.css` and
checked by `npm run lint:tokens`. Shared primitives live in
`frontend/components/ui`; new screens should reuse them before adding another
visual grammar.

## Verification

```bash
cd frontend
npm run lint:tokens
npm run typecheck
npm test
npm run build
```

Use browser testing for keyboard order, narrow/mobile layouts, source/citation
navigation, interrupted streams, worker failures, and reduced-motion behavior.
