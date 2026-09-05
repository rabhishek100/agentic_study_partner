# Read aloud

Decision date: 2026-09-06
Status: implemented. Verified by unit and component tests and by a browser
walkthrough of the controls; end-to-end synthesis against the live OpenRouter
voice has not been exercised.

## Purpose and agreed decisions

Every chat surface can speak its answers. A reader who is tired of the screen,
walking, or revising away from the desk should be able to hear an answer in a
voice that sounds like a person reading, with the figures the answer cites
described rather than silently skipped.

Four decisions were taken with the user before implementation:

1. **Verbatim, not rewritten.** The voice speaks what is on screen. It does not
   send the answer through a model to be re-explained for the ear. The only
   text the voice adds that the eye does not see is a spoken description of a
   cited figure — because a figure has no words on screen to read.
2. **Diagrams are described by a model, once, and cached.** A figure's ingest
   caption was written to be *searched*, not heard. A separate spoken
   description is generated per figure on first use and stored beside the
   caption, so every later play is free.
3. **"Read the last conversation" speaks the latest exchange** — the question,
   then the answer to it.
4. **OpenRouter TTS, chunked and cached**, reusing the path the interviewer
   voice already uses, with the browser's own speech synthesis as fallback.

Playback speed is adjustable and remembered.

## What "natural" means here, concretely

Naturalness is not only the voice model. Three things decide whether an answer
is listenable, and two of them are ours:

- **The script.** Markdown syntax, LaTeX source, and citation markers spoken
  literally ("asterisk asterisk", "backslash frac", "bracket S one") are what
  make machine reading intolerable. The narration script strips them and
  verbalises maths into words before a single character reaches the API.
- **Chunk boundaries.** Chunks are cut at sentence ends, never mid-clause, so
  the synthesiser sees whole sentences and produces whole-sentence prosody.
- **The voice model**, which stays environment-configurable
  (`OPENROUTER_READING_TTS_MODEL` / `_VOICE`), independent of the interviewer's,
  so the reading voice can be changed without touching interview behaviour.

## Scope

In version one:

- A play control on every recorded answer in the book conversation, side chats,
  and the video conversation.
- A control that speaks the latest exchange — question, then answer.
- A control that speaks the current text selection.
- Spoken figure descriptions inserted at the point the answer cites the figure.
- Adjustable speed (0.75x–2x), remembered across sessions.
- Pause, resume, and stop; one thing speaks at a time across the whole app.

Out of scope: speaking while an answer is still streaming, downloadable audio
files, per-voice user preference UI, and narration of interview turns (which
already has its own voice).

## Reuse boundaries

| Existing code | Reuse or extension |
|---|---|
| `interviews/speech.py` | The OpenRouter TTS call moves to `narration/synthesis.py`; `interviews/speech.py` keeps its interviewer-specific wording and delegates. Interview behaviour and its env vars are unchanged. |
| `ingestion/captions.py`, `image_blocks.base64_hash` | Spoken descriptions reuse the image identity captioning deduplicates on, in their own table, so narration owns its derived data and ingestion keeps owning captions. |
| `frontend/lib/citations.ts` | `splitOnCitations` and `figuresForMarker` decide where a figure's description belongs in the script. No new citation logic. |
| `frontend/hooks/use-interviewer-speech.ts` | The remote-audio-then-device-fallback shape is reused, generalised for a queue of chunks. |
| `frontend/components/side-chat/ask-selection.tsx` | The selection popover gains a second action rather than a second popover. |
| `VideoVisualCard.summary` | Video frames already carry model-written prose summaries, so the video surface speaks those directly and needs no new generation. |

## Where the script is built

The narration script is built in the client, in `frontend/lib/narration.ts`.

This is rendering, not generation. The same module boundary already puts
citation-marker resolution and LaTeX delimiter normalisation in the client:
turning a stored answer into something a person can take in is the interface's
job, and doing it for the ear is the same job as doing it for the eye. No claim
is introduced that the API did not ground — figure descriptions come from the
API, and the transform only removes syntax and reorders nothing.

The alternative — a script endpoint per surface — would need four endpoints
over three different turn stores to produce text the client already holds.

## The transform

Applied to the answer markdown, in order:

| Input | Spoken as |
|---|---|
| `## Heading` | "Heading." as its own sentence |
| `**bold**`, `_italic_` | the words, unmarked |
| `- item` | the item, ended with a period so the voice pauses |
| `` `identifier` `` | the identifier |
| ```` ```fenced``` ```` | "Code block, N lines, shown on screen." |
| markdown table | "Table with N rows, shown on screen." |
| `$\frac{a}{b}$` | "a over b" — see `spoken-math.ts` for the vocabulary |
| `[S1]`, `[N12:P84]` | nothing; the marker is a visual chip |
| a marker that cites a figure | after that sentence: "Figure, page 84. <spoken description>." |
| a figure no marker claimed | after the prose: "Also shown. Figure, page 84. <description>." |

Code blocks and tables are announced rather than read. Reading either aloud
character by character is not narration, and both are on screen for the reader
who wants them.

## API

- `POST /api/narration/figures` — body `{"figures": [{"book_id": 1, "block_id": 2}]}`,
  returns `{"descriptions": {"<block_id>": "…"}}`. Owner-scoped; generates what
  is missing, deduplicating by `content_hash` so one image is described once
  across the library. A figure that cannot be described returns its caption.
- `POST /api/narration/speech` — body `{"text": "…"}`, returns `audio/mpeg`.
  Cached by `sha256(text, model, voice)` per owner, so a replay costs nothing.

Both cap input length and both are refused without an owner.

## Persistence

`public.narration_figures` holds one spoken description per distinct image,
keyed by `(owner_id, image_blocks.base64_hash)` — the identity captioning
already deduplicates on, so a diagram reprinted in a second book is described
once, and an uncaptioned figure can still be described. Derived and rebuildable
by deleting rows.

`public.narration_audio` caches synthesised chunks: content hash, owner, model,
voice, media type, bytes, character count, cost, and `last_used_at`. Inserts
evict least-recently-used rows for that owner beyond a byte budget
(`NARRATION_AUDIO_CACHE_BYTES`, default 128 MB), so the cache cannot grow
without bound on a small database.

## Accessibility

The play control is a button with a text label, not an icon alone. Speaking
state is announced through a polite live region. Speed is a labelled select,
keyboard-operable. Nothing autoplays: audio starts only from a user gesture,
which is also what browsers require.
