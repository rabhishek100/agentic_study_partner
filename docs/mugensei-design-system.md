# Mugensei design system

The selected direction, formalized. This document is the contract; component
code implements it and does not extend it. Creative intent lives in
[`MUGENSEI_DESIGN_BRIEF.md`](../MUGENSEI_DESIGN_BRIEF.md) — where the two
disagree, the brief wins and this document is wrong and must be corrected.

Status: **approved and shipping.** Stages 1–6 are live; see
[Implementation staging](#implementation-staging).

Revision 3. Two design reviews ran against revision 1, and production itself
corrected revision 2 twice. All of it is recorded in
[Deviations and corrections](#deviations-and-corrections) rather than quietly
folded in — a contract that edits its own history is not a contract.

## The selected direction

**A fixed instrument frame carrying an asymmetric, evidence-driven
composition.** Three directions were explored; this is the *Kiban* frame with
grafts from *Ma* and *Kakejiku*.

- **The frame never moves.** A persistent rail, a working canvas, and one right
  region.
- **The right region is empty by default and fills with evidence.** This is the
  identity move: the page is asymmetric because grounding is what occupies the
  space.
- **Separation is ink value *or* a rule, never both.**
- **Serif means the reader's material** — the text under study and the reader's
  own words. Never system chrome.
- **One vermilion seal per screen.**

## Colour

### Semantic roles

Roles are the API. Components reference roles, never raw values, never a
route-specific colour, and never a derived value — no `color-mix`, no opacity
variants. If a tone is needed often enough to use, it is a role.

| Role | Dark | Light | Purpose |
|---|---|---|---|
| `canvas` | `#070B0A` | `#F4F0E6` | The ground. Sumi ink / washi paper. |
| `surface` | `#0D1714` | `#FFFDF7` | Quiet separation from the canvas. |
| `raised` | `#101A17` | `#FFFDF7` | Highest neutral layer. |
| `foreground` | `#F2EEE4` | `#17231F` | Reading text. |
| `muted` | `#A8C4B5` | `#586A63` | Secondary information. |
| `divider` | `#263930` | `#D5D7CB` | Decorative separation. **Not** a control boundary. |
| `border` | `#4A6F5D` | `#7F8175` | Control boundaries. Meets 3:1. |
| `action` | `#69D39F` | `#255E50` | Focus, selection, primary action, progress. |
| `action-hover` | `#8CDFB4` | `#1A4A3E` | The hover state of an action fill. |
| `action-on` | `#070B0A` | `#FFFDF7` | Text on an action fill. |
| `evidence` | `#8FC9AF` | `#1F5647` | Grounding and source relationships. |
| `wash` | `#1B4A38` | `#D8E9DF` | Selected / emphasized quiet surface. |
| `surface-hover` | `#16211D` | `#EAE5D8` | Neutral hover ground for quiet controls. |
| `disabled-surface` | `#0D1714` | `#F4F0E6` | Disabled control ground. |
| `disabled-foreground` | `#6B8579` | `#84897F` | Disabled control text. |
| `seal` | `#E97850` | `#AA482B` | The signature. Identity only. |
| `positive` | `#8FC9AF` | `#1F5647` | Success. Shares the evidence tone by design. |
| `warning` | `#E3B341` | `#7A5410` | Attention, not failure. |
| `destructive` | `#F0798C` | `#A32741` | Failure and destructive action. |

Every pairing was solved against its target. Worst cases: foreground on canvas
**17.08** / **14.23**; muted on canvas **10.58** / **5.05**; muted on wash
**5.39** / **4.55**; the weakest accent is light `seal` at **5.03**; control
border lowest **3.14**; action as a focus ring lowest **5.48**; action-hover
**12.56** / **8.82** with **12.56** / **9.86** for text on it; disabled
foreground **4.57** / **3.15**; markers on the wash **5.35**–**6.71**.

### Rules

**1. `divider` and `border` are different tokens.** At `#263930` the divider is
1.61:1 on canvas — correct for a hairline, far under the 3:1 a control boundary
needs. Using a divider on an input is an accessibility bug.

**2. `action` and `evidence` are not distinguishable by colour.** 1.02:1 apart,
identical under deuteranopia; not fixable with hex. They are separated by form
and placement: `action` appears on interactive controls, focus rings, and
progress indicators; `evidence` appears with a numeral or hairline underline on
non-interactive text. The tonal difference is a courtesy, not a signal.

**3. The `wash` never hosts a bordered control.** A control border cannot reach
3:1 against the dark wash without becoming a highlight. An emphasized row takes
its boundary from its own marker.

**4. The mark is outside the semantic system.** The logo uses jade for its path
because it is identity, not state. This is the single documented exception to
"jade carries meaning," and it applies to the mark only.

### Elevation reads differently per theme — deliberately

Sumi has the value range to separate planes tonally. Warm paper does not:
**every value above `#F4F0E6` tops out at 1.14:1**. So **dark separates by ink
value; light separates by hairline.** Same roles, same hierarchy, different
mechanism.

### Shadow and depth tokens

| Token | Dark | Light |
|---|---|---|
| `shadow-popover` | `0 8px 24px rgb(0 0 0 / 0.50)` | `0 8px 24px rgb(23 35 31 / 0.12)` |
| `shadow-floating` | `0 20px 48px rgb(0 0 0 / 0.60)` | `0 20px 48px rgb(23 35 31 / 0.16)` |

Docked surfaces have no shadow. Shadows are neutral and never carry hue.

### Z-index scale

`base 0` · `docked 10` · `sticky 20` · `floating-window 30` · `drawer 40` ·
`popover 50` · `dialog 60` · `toast 70` · `skip-link 80`.

Floating side-chat windows keep their own relative ordering *within* the
`floating-window` band. Nothing outside this scale may set a z-index.

### shadcn compatibility

shadcn/Radix primitives depend on `accent` (a hover *surface*), `secondary`,
`card`, and `sidebar-*`. Stage 1 keeps those names as **aliases onto roles**
(`card → surface`, `accent → surface-hover`, `sidebar → surface`, and so on) so the
primitives keep working. Aliases are a migration mechanism, not part of the
API — new code uses roles.

## Typography

| Face | Job |
|---|---|
| Source Serif 4 | The reader's material: answer prose, the reader's question, the titles they created. |
| Geist | The system: navigation, section names, controls, tables, metrics, status. |
| System mono | Identifiers, page and section references, timings, retrieval modes. |

**Serif carries the subject; sans carries the structure.** The brief asks for
serif on "major headings, long-form learning content, and reflective moments",
so the page's subject line is serif — "Cards", "Videos", "What would you like to
understand?" — as are reader-created titles and all material under study.

Sans carries everything structural beneath that: panel headings, section names,
table headers, labels, navigation, and every metric. The test is *is this the
subject, or the scaffolding around it?* — and there is exactly one subject per
screen.

(Revision 2 stated this as "headings are the system's words, so headings are
sans". That over-corrected: it put the page subject in sans, which the brief
explicitly assigns to the serif. The reviewer's actual objection was serif
leaking into small structural chrome, which is what the rule above forbids.)

Monospace marks genuinely technical content. A metrics row is not technical
content; a retrieval-mode identifier is.

### Scale

| Token | Size / line height | Face | Use |
|---|---|---|---|
| `display` | 34 / 1.18 | serif | Page subject; one per screen. |
| `title` | 26 / 1.25 | serif | Reader-created titles. |
| `heading` | 20 / 1.3 | sans | Section and panel headings — the scaffolding. |
| `prose` | `1em` of `--answer-size` / 1.75 | serif | Answer and reading text. |
| `body` | 16 / 1.5 | sans | Default interface text. |
| `ui` | 15 / 1.45 | sans | Controls, table cells, dense regions. |
| `meta` | 14 / 1.45 | sans | Metadata. **The information floor.** |
| `eyebrow` | 12 / 1, `0.14em`, uppercase | sans | Region labels only. |
| `mono` | 14 / 1.45 | mono | Identifiers and timings. |

**Nothing below 14px carries information.** The brief sets this floor and it
wins. `eyebrow` at 12px is the sole exception and is reserved for labels naming
a region whose content already says the same thing — removing an eyebrow may
never remove meaning.

**Prose stays em-relative.** `.answer-prose` is deliberately sized in `em`
against `--answer-size`, with container-query steps, so the same answer reads
correctly in a 300px side-chat window and a 700px one. A fixed prose size would
destroy that. The scale supplies the *default* value of `--answer-size`; the
container steps stay.

- Prose measure capped at `68ch`, applied to reading blocks inside the canvas,
  never to the frame.
- Weights 400 / 500 / 600 only.
- Tabular numerals wherever digits align.

## Space, shape, depth

**Spacing** — 4px foundation. Permitted: `4, 8, 12, 16, 24, 32, 48, 64`.
Layout uses flex/grid `gap`, not per-element margins.

**Radii** — `menu 6`, `control 8`, `panel 10`, `floating 14`, `pill full`.

**Elevation** — `flat` (docked, no shadow) · `popover` (1px border +
`shadow-popover`) · `floating` (1px border + `shadow-floating`).

**Icons** — Lucide, **1.5px stroke, sized 16 / 20 / 24 only**. Every icon-only
control carries an accessible name. A concept→icon map lives beside the icon
component so routes cannot each pick their own.

## States

Every interactive component defines all of these. They are specifications, not
defaults.

| State | Treatment |
|---|---|
| `default` | Role colours as declared. |
| `hover` | Fill moves to `action-hover`; ghost and quiet controls move ground to `surface-hover`. Never a size or position change. |
| `pressed` | Same ground as hover, no transition — the press must feel immediate. |
| `focus` | 2px `action` outline at 2px offset. **One treatment everywhere.** |
| `selected` | `wash` ground **plus** a 3px `action` marker **plus** `aria-selected` / `aria-current`. Never colour alone. |
| `disabled` | `disabled-surface` ground, `disabled-foreground` text, `aria-disabled`. Never removed from the tab order without a replacement affordance. |
| `read-only` | Default ground, `border` replaced by `divider`, `readonly`. Distinct from disabled: it is copyable. |
| `indeterminate` | Checkbox: a bar, not a tick. Progress: a hairline at `divider` with no fill and `aria-valuenow` omitted. |
| `error` | `destructive` border, `aria-invalid`, message below bound by `aria-describedby`. |

**Selection and focus must never share an appearance.** Selection is ground plus
marker; focus is the offset outline. Both may be present at once and must remain
separately readable.

**Skeleton vs spinner.** A skeleton is used where the *shape* of the incoming
content is known and stable — it is a static `surface` block, never animated,
and its container carries `aria-busy="true"`. A spinner is used only where
duration is unknown and shape is not, and it is the one permitted looping
animation in the system. Neither is used for operations under 200ms.

## Layout and responsive behaviour

### The frame

```
┌──────────────────────────────────────────────────────┐
│ masthead · 56px · mark + wordmark + status           │
├────────┬─────────────────────────────┬───────────────┤
│ rail   │ canvas                      │ right region  │
│        │  (prose capped at 68ch)     │  (see modes)  │
└────────┴─────────────────────────────┴───────────────┘
```

The masthead is **56px**, matching `HEADER_INSET` in `lib/floating-window.ts`,
which is the origin every floating side-chat window is clamped, snapped, and
cascaded against.

That coupling is **real but currently untested**: the floating-window tests
import the `HEADER_INSET` symbol and assert against it, so they pass at any
value, and no test asserts the rendered header height. Stage 3 must land a test
that binds the rendered masthead height to `HEADER_INSET`. A second hazard:
`h-14` is rem-based while `HEADER_INSET` is compared against raw pixel
coordinates, so a raised browser font size puts windows under the header.
Stage 3 resolves this by measuring the header rather than assuming it.

### The right region has one mode enum

`evidence | document | activity | transcript | none`

The evidence gutter, the docked PDF, the generation-activity panel, and the
video transcript are **one region in different modes**, never two regions
competing for the right edge. `none` is the default, and it is margin rather
than a collapsed panel.

The contract, in detail:

- **Every mode stays mounted.** Modes are toggled with `hidden`, never
  conditionally rendered. `document → evidence → document` must not unmount the
  PDF viewer: `split-pane.tsx` records the real bug this causes — restarted
  playback and re-fetched authenticated images. `FloatingWindow` already does
  this correctly and is the pattern to copy.
- **Width is per mode.** `evidence` and `activity` are fixed at 280px;
  `document` and `transcript` are resizable and keep their own storage key and
  range. One shared key across all modes is wrong.
- **The label is per mode**, not the hardcoded `aria-label="Source document"`.
- **One mode at a time.** Requesting a mode replaces the current one; the
  previous mode's scroll position and state survive because it stays mounted.

Two existing behaviours must change for this to hold, and both are safe:

1. `app-shell.tsx` gates the desktop rail on `!aside`, so the rail *disappears*
   whenever a document opens — directly contradicting "the frame never moves."
   The rail becomes independent of the right region.
2. `split-pane.tsx` applies `hidden md:flex` to the canvas when `aside` is set,
   so opening evidence below `md` would blank the conversation. Below the
   `medium` breakpoint the right region becomes an overlay instead.

**The canvas may split; the right region may not.** The video route runs a
second resizable pane (player / ask) *inside* the canvas. That stays a canvas
concern. This keeps "one right region" true without pretending the video route
is simpler than it is.

### Breakpoints, in `em`

| Name | Width | Behaviour |
|---|---|---|
| `compact` | `< 48em` | Frame dissolves. One task, bottom tab bar, composer anchored. Right region becomes a bottom sheet. |
| `medium` | `48–64em` | Rail collapses to icons. Right region becomes an overlay drawer. |
| `wide` | `≥ 64em` | Full frame. |

`em` rather than device widths, so a reader at 200% zoom gets the compact
layout. `FLOATING_MIN_VIEWPORT_WIDTH = 1024` is a raw-pixel breakpoint and must
be reconciled with `64em` in stage 4.

## Accessibility contract

- **Contrast.** AA in every theme and state; values above are solved.
- **Focus.** 2px `action` outline at 2px offset, everywhere.
- **Selection is not focus.**
- **Never colour alone.** Every status carries a glyph or label. This includes
  the compact tab bar.
- **Targets.** 44px preferred, 24px absolute floor, no overlap. Citation markers
  are the one exception, and a sanctioned one: WCAG 2.5.8 exempts targets inline
  in a sentence, which is exactly what they are. Their hit area is still padded
  vertically to 24px, and every marker has a full-size counterpart in the
  evidence list, so no source is reachable only through a superscript.
- **Zoom.** Legible and operable at 200%, delivered by the `em` breakpoints.
- **Density.** Dense tables get row tracking — but tonal striping **or**
  hairlines, never both.
- **Forms.** `aria-invalid` plus `aria-describedby`, message directly below the
  field, and a summary when a form fails as a whole.
- **Logical properties.** The selection grammar is an inline-start marker, not a
  left one.

## Enforcement

The contract is only real if it cannot be violated silently. Stage 1 lands:

- a colour allowlist (no hex, no `color-mix`, no bare `opacity` in components);
- a spacing-interval check;
- a font-size allowlist;
- a radius and icon-size allowlist.

## Component inventory

**Frame** — Masthead, Rail, Canvas, RightRegion, BottomSheet, TabBar.
**Surface** — Panel, Region, Divider, Popover, Dialog, FloatingWindow.
**Type** — Prose, Display, Title, Heading, Eyebrow, Meta, Mono.
**Control** — Button, IconButton, Input, Textarea, Select, Checkbox, Collapsible,
Menu, Tooltip, ConfirmDialog.
**Data** — Table, Row, MetricCell, Progress, StatusChip, Badge, Skeleton.
**Product** — CitationMarker, EvidenceRow, RetrievalTrace, Composer, StreamedAnswer.

`ui/card.tsx` is retired for `Panel` / `Region` — "card" is the pattern the
brief rejects. Note `bg-card` is referenced directly in `app-shell.tsx` and
`floating-window.tsx`, so the alias must survive until stage 5 completes.

## Implementation staging

Reordered after review. Each stage preserves behaviour, runs the test suite, and
is verified visually in both themes at all three breakpoints before the next
begins.

| # | Stage | Biggest risk |
|---|---|---|
| 1 | **Tokens + enforcement + literal inventory.** Roles, both themes, shadcn aliases, lint rules. Grep and record every colour literal now rather than at the end. | Radix primitives losing hover states if aliases are incomplete. |
| 2 | **Panel / Region.** Retire `Card`. Defines the padding that stage 3 then applies. | Padding and radius shift across decks, interviews, papers, videos at once. |
| 3 | **Type and space.** The scale, the measure, the intervals — preserving `.answer-prose`'s em-relative container scaling. | Breaking side-chat prose scaling. |
| 4 | **Mark and masthead.** All four logo forms; land the header-height test. | The untested 56px coupling. |
| 5a | **RightRegion, `document` mode only.** Always-mounted modes, `hidden` toggling, per-mode width and label. Decouple the rail from `aside`; fix the canvas `hidden md:flex`. | The remount bug. This is the highest-risk stage. |
| 5b | **Remaining modes, one route at a time.** evidence, activity, transcript. | The video route's nested canvas split. |
| 6 | **States.** By family: hover/pressed, then disabled/read-only, then error/indeterminate, then loading. | Scope — ~40 test files touch these. |
| 7 | **Sweep.** Delete the remaining literals; full verification pass. | Nothing should be left; if it is, stage 1's inventory was wrong. |

Verification is a gate on every stage, not a final stage of its own.

Deployment is not part of any stage. Production deploys happen only on explicit
request.

## Deviations and corrections

Declared rather than silent.

**Deviations from the brief**, all to meet a contrast target the brief's
starting values missed:

| Role | Brief | Here | Why |
|---|---|---|---|
| light `evidence` | `#246653` | `#1F5647` | Separation from light `action`. |
| light `muted` | `#586B63` | `#586A63` | 4.4997:1 on the wash — under target by a hair. |
| light `seal` | `#AD492C` | `#AA482B` | 4.90:1 was too thin a margin. |
| `border` | one value | split into `divider` + `border` | 1.61:1 cannot bound a control. |

The spacing scale adds `64`. The brief's `focus` role is folded into `action`,
since the focus ring and the action colour are the same value by design.

**Corrected in revision 2**, after review:

- The information floor was 13px; the brief says 14px. The brief wins.
- Revision 1 claimed the 56px masthead was asserted in tests. It is not — the
  tests assert against the imported symbol. Corrected above, with a test added
  to stage 3.
- The right-region enum had no mounting contract, so it would have reintroduced
  the documented remount bug. Contract added.
- The rail-disappears-with-`aside` and canvas-`hidden md:flex` behaviours were
  not accounted for at all.
- `heading` was serif, contradicting "serif is not a heading flourish." Headings
  are system words and are now sans.
- Rule 2 forbade `action` on non-interactive elements while the brief lists
  progress among jade's meanings. Progress is now explicitly included.
- Missing roles added: `action-hover`, `disabled-surface`,
  `disabled-foreground`, shadow tokens, z-index scale.
- Missing state specs added: hover, pressed, read-only, indeterminate, and the
  skeleton-vs-spinner policy.

**Corrected in revision 3**, after seeing the system against real content:

- Hover was specified as moving quiet controls to the `wash`. Shipped, that
  turned every menu item, list row, and quiet fill jade, because `wash` is the
  *selected* ground and hover is not selection. A neutral `surface-hover` role
  now carries hover, and the wash goes back to meaning selected — which is what
  keeps it rare.
- "Headings are the system's words, so headings are sans" over-corrected; the
  brief assigns serif to major headings and reflective moments. The rule is now
  subject-versus-scaffolding, recorded above.
- Migration order corrected; the frame stage split in two.
