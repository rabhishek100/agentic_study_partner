# Mugensei — Brand and Visual Design Brief

## How to use this document

This is the canonical creative brief for an agent redesigning the application
from `main`.

- Treat previous UI implementation branches as discarded experiments.
- Do not port layouts, components, navigation decisions, or CSS from those
  branches merely because they already exist.
- Preserve the established name, brand meaning, logo concept, and visual
  philosophy described here.
- Begin with visual exploration and a coherent design system before writing
  production UI code.
- This document deliberately avoids prescribing product features or screen
  architecture. Derive those from the product requirements on `main`.

## The assignment

Create a distinctive, highly readable visual identity and interface system for
an intelligent study companion. It should feel modern, disciplined, editorial,
and quietly futuristic. It must be minimal enough to support concentration but
not so minimal that it feels generic, empty, or unfinished.

The central creative tension is:

> Japanese editorial restraint and contemplative craft, combined with the
> precision and energy of a sophisticated computational system.

The result should not look like a conventional SaaS dashboard, a cyberpunk
game, an anime product, or a generic AI chatbot.

## Brand name

The chosen name is **Mugensei**.

Pronunciation may be presented simply as “moo-gen-say” when needed. Do not rely
on a kanji spelling or claim a literal Japanese translation without linguistic
validation. The name is intended as an original, Japanese-influenced brand
evoking *mugen*—limitlessness or infinity—and an ongoing state of becoming.

### Meaning

Mugensei represents:

- limitless learning;
- an endless path toward mastery;
- disciplined progress rather than instant expertise;
- turning difficult material into connected understanding;
- mastery as a continuing practice, not a finish line.

### Brand promise

**Master difficult material.**

### Brand philosophy

**The endless path to mastery.**

### Emotional qualities

The brand should feel:

- calm;
- intelligent;
- focused;
- rigorous;
- trustworthy;
- contemplative;
- quietly ambitious;
- futuristic without spectacle.

It should not feel cute, loud, mystical, ornamental, militaristic, or
overwhelmingly technical.

## Logo direction

The approved logo concept combines an open book with a single upward,
continuous path. The path rises beyond the book, suggesting learning without a
terminal point. Three small nodes along the path represent evidence becoming
connected understanding. A restrained vermilion square acts as a signature or
seal-like compositional accent.

Primary visual reference:

`/Users/abhishek/.codex/generated_images/01a00270-ad24-76b2-b85d-64bd85903722/exec-14b3425e-e44f-4ec2-b15f-ca3a1ec2b648.png`

The reference establishes the concept, not necessarily the final production
vector geometry. Reconstruct and refine it professionally rather than tracing
visual noise from the raster image.

### Logo principles

- Preserve the open-book-to-infinite-path idea.
- Preserve the three evidence nodes.
- Preserve the rare vermilion signature accent.
- Ensure the compact mark remains identifiable at 24 CSS pixels.
- Produce a compact mark, horizontal lockup, monochrome version, and versions
  suitable for both dark and light surfaces.
- Favor elegant, controlled curves and balanced negative space.
- The wordmark should feel literary and premium, not corporate or ornamental.
- The symbol and wordmark should work independently.

### Logo prohibitions

- No infinity symbol pasted onto a book.
- No graduation caps, brains, light bulbs, robots, sparkles, or generic AI
  motifs.
- No literal Japanese characters used as decoration.
- No imitation calligraphy or faux-cultural symbols.
- No neon glow, chrome effects, bevels, or gaming aesthetics.
- No large red fills; the vermilion accent must remain scarce.
- Do not place the mark inside a generic rounded-square app tile unless the
  platform specifically requires an app icon.

## Overall art direction

The design language is **calm futurism**: editorial clarity with computational
precision.

Draw inspiration from:

- Japanese books, prints, ink, paper, seals, proportion, and negative space;
- contemporary editorial design;
- dark terminals and the Matrix palette only at the level of restrained green
  light on deep ink surfaces;
- carefully made instruments and research tools;
- quiet layers, fine rules, and deliberate alignment.

Japanese influence should be expressed through restraint, composition,
material color relationships, hierarchy, and craft—not through decorative
stereotypes.

“Matrix-inspired” means deep near-black environments, luminous but readable
green accents, precision, and a sense of intelligent systems. It does **not**
mean code rain, pure black with fluorescent green everywhere, glow effects, or
low-contrast hacker styling.

## Color philosophy

The signature environment is a dark, sumi-ink-inspired theme with warm text and
controlled jade accents. A warm-paper light theme must be equally complete and
intentional.

### Core dark palette

| Role | Starting value | Intent |
|---|---:|---|
| Sumi background | `#070B0A` | Deep ink, not flat digital black |
| Raised surface | `#0D1714` | Quiet separation without card clutter |
| Popover surface | `#101A17` | Highest neutral layer |
| Warm foreground | `#F2EEE4` | Paper-like, low-glare reading text |
| Muted foreground | `#A8C4B5` | Celadon-tinted secondary information |
| Border | `#263930` | Fine structural separation |
| Action jade | `#69D39F` | Focus, selection, and primary action |
| Evidence jade | `#83D0AD` | Trust, grounding, and source relationships |
| Deep jade | `#153B2D` | Selected and emphasized quiet surfaces |
| Vermilion seal | `#E97850` | Rare identity accent |

### Core light palette

| Role | Starting value | Intent |
|---|---:|---|
| Warm paper background | `#F4F0E6` | Washi-like reading canvas |
| Raised paper | `#FFFDF7` | Clean elevated surface |
| Ink foreground | `#17231F` | Soft black-green text |
| Muted foreground | `#586B63` | Secondary copy |
| Action jade | `#255E50` | Primary interaction |
| Evidence jade | `#246653` | Grounded and source-related information |
| Soft jade | `#D8E9DF` | Quiet emphasis |
| Vermilion seal | `#AD492C` | Rare identity accent |
| Border | `#D5D7CB` | Subtle structure |

These values are starting tokens, not permission to create dozens of arbitrary
opacity variants. Build semantic roles—canvas, surface, raised surface,
foreground, muted, border, action, evidence, positive, warning, destructive,
focus, and seal—and validate every pairing for accessibility.

### Color rules

- Jade carries meaning: progress, selection, focus, evidence, and successful
  grounding.
- Vermilion is a signature, not a second primary color. Use it very rarely.
- Destructive/error red must be a separate semantic token from vermilion.
- Avoid large saturated fields, gradients used for decoration, and glowing
  borders.
- Avoid excessive green text on dark backgrounds; most reading text should be
  warm neutral.
- Dark and light modes must preserve hierarchy and meaning rather than merely
  invert colors.
- Additional themes may be explored later, but each must preserve the semantic
  roles and accessibility contract. Prefer a small curated set over unlimited
  customization.

## Typography

Use typography to combine editorial depth with modern interface clarity.

- **Source Serif 4** or an equivalently readable contemporary serif for major
  headings, long-form learning content, and reflective moments.
- **Geist** or an equivalently precise modern grotesk for navigation, controls,
  forms, metadata, and compact UI.
- A system or carefully selected monospace only for code, citations, technical
  identifiers, and diagnostics.

The serif is not decoration; it signals sustained reading and considered
knowledge. The sans serif carries interaction and system clarity. Monospace
signals genuinely technical material, never general futurism.

### Typographic principles

- Use a restrained scale with clear hierarchy.
- Favor medium weights and excellent spacing over oversized bold headings.
- Keep long-form reading measures near 60–70 characters per line.
- Use relaxed line height for prose.
- Keep meaningful body text at least 14px; never make essential metadata tiny.
- Uppercase, letter-spaced eyebrow labels may be used sparingly.
- Avoid mixing numerous typefaces, weights, or arbitrary font sizes.

## Composition and layout

The interface should feel composed rather than decorated.

- Use a consistent 4px spacing foundation, with primary intervals of 4, 8,
  12, 16, 24, 32, and 48px.
- Establish strong alignment and predictable spatial rhythm.
- Use negative space deliberately, but maintain useful information density.
- Prefer fine dividers, tonal surface changes, and typography over nested cards.
- Let important reading or working surfaces feel expansive.
- Make hierarchy obvious without relying on color alone.
- Responsive layouts should recompose around the user’s task, not merely shrink
  desktop columns.
- Every visible region must have a clear purpose; never add panels solely to
  satisfy a template.

The design should be minimal but not barren. Richness should come from real
content, typography, rhythm, source material, progress, and thoughtful states—not
from ornamental containers.

## Shape, borders, depth, and iconography

- Use restrained radii: approximately 6px for menus, 8px for controls, 10px for
  cards or panels, and up to 14px for major floating surfaces.
- Reserve fully rounded shapes for genuine pills, tags, avatars, and compact
  statuses.
- Use 1px borders and tonal contrast as the main separation method.
- Docked surfaces should generally have no shadow.
- Popovers may use a controlled medium shadow; dialogs and truly floating
  objects may use a larger shadow.
- Use one coherent outline icon family, preferably Lucide or equivalent.
- Icons should communicate function, not provide decoration.
- Avoid glassmorphism, excessive blur, embossed surfaces, neon outlines, and
  stacks of rounded cards.

## Motion

Motion should explain state, direction, continuity, and progress.

- Keep transitions short, restrained, and physically coherent.
- Use motion to reveal structure, connect related states, or confirm an action.
- Avoid ambient motion behind reading content.
- Avoid looping decorative animation, glowing pulses, and dramatic parallax.
- Support `prefers-reduced-motion`; no meaning may depend on animation.

## Accessibility and readability

Accessibility is part of the visual philosophy, not a final QA step.

- Meet WCAG AA contrast in every theme and state.
- Maintain a highly visible, consistent keyboard focus treatment.
- Do not communicate status using color alone.
- Preserve legibility at 200% zoom and narrow viewports.
- Prefer 44px touch targets; never fall below the applicable accessibility
  minimum.
- Ensure text remains comfortable during long study sessions.
- Avoid visual noise and unnecessarily high contrast across large areas.
- Design loading, empty, error, disabled, selected, and focused states as part
  of the same system.

## Brand voice

The voice is direct, composed, encouraging, and intellectually honest.

Prefer language such as:

- “Ready to study.”
- “Grounded in three sources.”
- “Continue your path.”
- “Evidence is insufficient.”

Avoid:

- mascot-like chatter;
- exaggerated AI claims;
- gamified hype;
- mystical pseudo-Japanese language;
- aggressive productivity language;
- dense internal-system jargon in ordinary UI copy.

The product should feel like a disciplined guide and a well-made instrument,
not an entertaining assistant persona.

## Consistency principles

- Consistency means shared rules and predictable behavior, not forcing every
  screen into an identical layout.
- Reuse tokens, components, interaction grammar, typography, and spacing.
- Allow different tasks to have different compositions when their needs differ.
- Every exception should be intentional and explainable.
- Build primitive components from semantic roles rather than route-specific
  colors and arbitrary styling.
- Treat responsive behavior, keyboard behavior, and system states as part of
  the component definition.

## What success looks like

A successful Mugensei design should be recognizable without its logo because
of its warm ink-and-paper contrast, disciplined jade semantics, editorial serif
voice, precise sans-serif controls, subtle seal accent, and calm spatial
rhythm.

It should look credible beside serious research and reading tools while still
having a memorable identity. It should invite long, focused sessions, make
complex material feel navigable, and communicate that mastery is built through
connected evidence and deliberate practice.

## Required process for the next agent

1. Start from `main` and inspect product requirements without importing UI
   implementation from discarded branches.
2. Use this brief and the logo reference as the creative foundation.
3. Define semantic tokens and typography before designing individual screens.
4. Produce three genuinely different visual directions that all honor this
   brief; do not code them yet.
5. Review the directions for brand fit, readability, accessibility, responsive
   logic, and cross-screen coherence.
6. Ask the user to select or combine a direction.
7. Formalize the selected direction into a compact design system and a small set
   of representative responsive screen states.
8. Only then implement, testing visual consistency and accessibility throughout.

Do not mistake existing code for approved design. The durable decisions are the
Mugensei identity, the endless-mastery philosophy, the book/path/nodes/seal logo
concept, calm-futurist Japanese editorial influence, the ink/paper/jade palette,
the serif-plus-modern-sans typography, restrained geometry, and an unwavering
commitment to readability and accessibility.
