# Design QA — Mugensei Books, Option 3

## Scope

- Source visual truth: `artifacts/design-qa/mugensei-books-option-3/source-option-3.png`
- Implemented route: `/`
- Deterministic local QA state: `/?design-preview=books` (development only)
- Desktop viewport: 1440 × 1024 CSS px at 1× density
- Mobile viewport: 390 × 844 CSS px at 1× density
- Source pixels: 1487 × 1058. Implementation pixels: 1440 × 1024. For the comparison board, the implementation was normalized to 1487 × 1058; both images have the same 1.405 aspect ratio within rounding.
- State: signed-in dark theme, one completed grounded answer, source page 142 open, two cited evidence items.

## Evidence

- Final full-view comparison: `artifacts/design-qa/mugensei-books-option-3/comparison-desktop-final.jpg`
- Final focused answer comparison: `artifacts/design-qa/mugensei-books-option-3/comparison-answer-focused.jpg`
- Final desktop implementation: `artifacts/design-qa/mugensei-books-option-3/implementation-desktop-final.jpg`
- Mobile implementation: `artifacts/design-qa/mugensei-books-option-3/implementation-mobile.jpg`
- Signed-in production verification: `artifacts/design-qa/mugensei-books-option-3/production-empty-state.jpg`

## Comparison history

### Pass 1 — blocked

- **[P2 · Layout]** The evidence rail was 208 px wide, leaving the document page visibly narrower than the source. Reduced the rail to 160 px while retaining the selected 45/55 answer-to-source split.
- **[P2 · Typography and density]** The answer initially used the app's generic serif reading body and browser list markers. Added the selected editorial hierarchy: serif learning headings, sans explanatory prose, numbered jade circles, and stronger list rhythm.
- **[P2 · Context hierarchy]** The first study bar pushed both controls to the far edge and repeated status under the logo. Rebalanced the book/chapter tracks, kept controls adjacent to context, enlarged the brand title, and removed redundant status from this workspace.
- **[P2 · Source toolbar]** The production PDF viewer used separate title and control rows. Consolidated page, zoom, minimize, and close controls into one 48 px toolbar; linked video documents retain their visible resource title.
- **[P2 · Mobile accessibility]** Icon-only Change book and Conversations controls had no accessible names at the compact breakpoint. Added explicit labels and rechecked the 390 px layout.

### Final pass — passed

- **Fonts and typography:** Source Serif 4 remains the editorial voice for the question, answer heading, and principle titles; Geist handles navigation, labels, evidence, and long-form answer copy. Size, weight, wrapping, and line-height match the selected hierarchy without clipped text.
- **Spacing and layout rhythm:** Desktop preserves the 45/55 answer/source division, 80 px context bar, single-row PDF toolbar, full-height composer, and narrow evidence rail. Mobile collapses to a full-width source reader with no horizontal overflow.
- **Colors and tokens:** Sumi black, warm bone, restrained jade, fine green-gray borders, and the warm paper surface match the selected direction. Existing AA-oriented tokens and jade focus ring remain unchanged.
- **Image and asset fidelity:** The real product path continues to render authenticated source bytes with `react-pdf` and real text-layer citation highlights. The local fixture is development-only and exists solely to reproduce the selected QA state when no local book is uploaded; it is not shipped as a production document replacement.
- **Copy and content:** Study-context labels, grounded-question language, evidence labels, composer copy, and the training-serving-skew example are coherent and match the selected screen's purpose.
- **Icons:** Lucide supplies one consistent fine-line icon family. No custom SVG, emoji, or placeholder icon art was introduced.
- **States and interactions:** Browser-verified Change book drawer access, citation navigation from page 142 to page 147, active evidence state, document page controls, and the grounded-question composer. The real answer flow auto-opens the first cited source once per completed turn but respects a reader closing it.
- **Accessibility:** Keyboard-operable separator and citations remain intact; icon-only controls have names; source regions and evidence index have landmarks; focus styling and reduced-motion support remain inherited from the design system.
- **Browser errors:** The final local browser pass reported no console warnings or errors.
- **Production verification:** Railway deployment `d7eb577f-2fdb-40a3-999a-00d1f01fc5ba` succeeded. The signed-in production route loaded the deployed header, navigation, upload controls, and empty state with no app-originated console errors. That account currently contains no books, so the populated study state was verified locally against the deterministic fixture and covered by the production build rather than fabricated in production.
- **Focused comparison:** The answer crop was compared separately because heading treatment, numbered principles, citation chips, and composer density were too small to judge confidently in the full board.

## Verification

- `npm run typecheck` — passed
- Focused component suite — 23 tests passed
- Full frontend suite — 416 tests passed
- `npm run build` — passed

## Residual P3 polish

- The live interface keeps compact Copy, Ask on the side, Regenerate, and answer-inspector controls that the static mock omits. They are existing functional utilities and do not alter the selected hierarchy.

## Follow-up — contextual strip visibility

- User-reported source: `artifacts/design-qa/mugensei-books-context-strip/source-redundant-strip.png` (3840 × 238 px).
- Corrected implementation: `artifacts/design-qa/mugensei-books-context-strip/implementation-sidebar-state.jpg` (1440 × 900 CSS px at 1× density).
- Focused before/after comparison: `artifacts/design-qa/mugensei-books-context-strip/comparison-context-strip-final.jpg`. The implementation's top 128 CSS px were normalized to the source width for this focused structural comparison.
- **[P2 · Information architecture, resolved]** The context strip repeated book selection and conversations while the persistent library sidebar already exposed both. It now renders only in focused study mode, where the PDF/evidence workspace replaces the sidebar.
- **Responsive behavior:** Outside focused study mode, the normal sidebar is visible at desktop widths and the standard drawer trigger remains available below the desktop breakpoint.
- **Regression evidence:** Browser checks confirmed `standardHasContext: false`, `focusedHasContext: true`, and `focusedHasSidebar: false`. No local console warnings or errors were reported.
- **Production verification:** Railway deployment `b67fa581-6d6b-46be-b82c-5ff827f449e1` succeeded. The signed-in live sidebar state reported no Study context region and no app-originated console warnings or errors.

final result: passed
