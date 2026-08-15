# Mugensei brand system

## Brand idea

**Mugensei** is a calm-futuristic study companion built around limitless
becoming: difficult material becomes mastery through deliberate, evidence-led
practice.

- Promise: **Master difficult material.**
- Philosophy: **The endless path to mastery.**
- Proof: answers, summaries, and practice remain visibly grounded in sources.
- Personality: calm, disciplined, intelligent, quietly futuristic.

The Japanese influence should come through restraint, proportion, ink-and-paper
color relationships, and editorial craft. Avoid decorative cultural shorthand,
anime styling, literal Matrix code rain, and generic AI imagery.

## Logo

The open book becomes a single upward path that continues beyond the page.
Three nodes represent evidence becoming connected understanding. The small
vermilion square is a signature inspired by the compositional role of a seal,
not an imitation of a traditional seal or written character.

- Dark-theme full lockup: `/brand/mugensei-lockup.png`
- Light-theme full lockup: `/brand/mugensei-lockup-light.png`
- Compact mark: `/brand/mugensei-mark.png`
- Minimum compact-mark size: 24 CSS pixels.
- Keep clear space equal to at least one evidence-node diameter.
- Do not recolor individual nodes, add glow, place the mark in a generic rounded
  tile, or use the old `ASP` monogram.

## Color system

### Light — warm paper

| Role | Value | Use |
|---|---:|---|
| Background | `#f4f0e6` | Reading canvas; warm washi-like base |
| Card | `#fffdf7` | Raised reading and control surfaces |
| Foreground | `#17231f` | Primary ink text |
| Muted foreground | `#586b63` | Secondary copy and metadata |
| Primary jade | `#255e50` | Primary actions and selected controls |
| Evidence jade | `#246653` | Citations, references, grounded states |
| Soft jade | `#d8e9df` | Evidence chips and quiet emphasis |
| Vermilion seal | `#ad492c` | Rare brand signature; never large fills |
| Border | `#d5d7cb` | Dividers and component boundaries |

### Dark — indigo ink

| Role | Value | Use |
|---|---:|---|
| Background | `#08100f` | Signature dark study environment |
| Sidebar | `#0b1517` | Slightly cooler navigation field |
| Card | `#101b18` | Layered content surface |
| Foreground | `#f2eee4` | Warm, low-glare reading text |
| Muted foreground | `#a3b1aa` | Secondary copy and metadata |
| Primary jade | `#8bd5b4` | Primary actions and focus |
| Evidence jade | `#83d0ad` | Citations and grounded states |
| Soft jade | `#183b30` | Evidence chips and selected surfaces |
| Vermilion seal | `#e97850` | Rare identity accent and signature |
| Border | `#293b35` | Dividers without high-contrast boxes |

Jade has semantic meaning: evidence, progress, selection, focus, and successful
grounding. Vermilion is intentionally scarce so the logo's seal and exceptional
brand moments retain their force. Destructive states use a separate red token.

## Typography

- **Source Serif 4:** chapter titles, answer prose, major headings, and reflective
  learning moments.
- **Geist:** navigation, controls, forms, labels, and all compact interface text.
- **System monospace:** citations, retrieval metadata, code, and diagnostics only.

Long-form prose should stay at or below roughly 65 characters per line, with a
relaxed line height. Monospace is evidence of system detail, not decoration.

## Shape, spacing, and layering

- Base radius: `0.625rem`; reserve larger radii for dialogs and major surfaces.
- Prefer spacing, alignment, typography, and fine dividers over nested cards.
- Use indigo/ink and paper surfaces as the hierarchy. Do not add gratuitous
  shadows, glass effects, or neon borders.
- The interface may feel rich through typography, quiet layering, source
  previews, diagrams, and real information—not decorative UI density.

## Motion and accessibility

- Motion explains state: opening evidence, continuing a path, or confirming
  progress. Avoid ambient motion behind reading content.
- Respect `prefers-reduced-motion`; no core meaning may depend on animation.
- Maintain WCAG AA contrast, visible keyboard focus, and full keyboard operation.
- Dark is the signature theme; light is a complete warm-paper reading theme, not
  a secondary afterthought.

## Voice

Use direct, composed language. Prefer “Ready to study,” “Grounded in three
sources,” and “Evidence is insufficient” over playful assistant chatter or
system-heavy jargon. The product should feel like a disciplined guide rather
than a mascot.
