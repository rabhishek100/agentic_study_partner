# Interface

The Next.js client presents the grounded API: it selects scope, renders source
evidence, submits commands, and plays media. It does not generate study claims.

## Rendering and data fetching

The interface uses the Next.js App Router with client-side data fetching and
a predominantly client-rendered interactive UI. All current route pages declare
`"use client"`; the root layout remains a Server Component that supplies the
document, metadata, fonts, and client providers.

`"use client"` defines the client component boundary; it does not disable
initial HTML rendering. On a full page load, Next.js prerenders the initial UI,
including the session-loading state. Browser JavaScript then hydrates that HTML
(attaches interactivity), restores the Supabase session, and fetches study data.
For example, the library page loads books in a `useEffect` after a session is
available. User-specific library and conversation data are therefore loaded
and rendered in the browser rather than supplied by the root Server Component.
Subsequent client-side navigation renders Client Components in the browser.

Ordinary JSON requests use the browser's `apiFetch` helper, which attaches the
access token and calls `/api`. Next.js rewrites those requests to FastAPI.
That proxy handles transport; FastAPI performs retrieval and study generation,
and the browser renders the results.

Code: [root layout](../frontend/app/layout.tsx),
[library page](../frontend/app/page.tsx),
[session hook](../frontend/hooks/use-session.ts),
[API helper](../frontend/lib/api.ts),
[proxy configuration](../frontend/next.config.mjs).

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
