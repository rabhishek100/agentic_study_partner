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
- **Spacing and layout rhythm:** Desktop preserves the 45/55 answer/source division, compact context bar, single-row PDF toolbar, full-height composer, and narrow evidence rail. Mobile collapses to a full-width source reader with no horizontal overflow.
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

## Follow-up — consolidated Books navigation

- User-reported source: `artifacts/design-qa/mugensei-books-hamburger/source-large-context-controls.png` (914 × 238 px).
- Browser-rendered implementation: `artifacts/design-qa/mugensei-books-hamburger/implementation-focused.png` (1440 × 900 CSS px at 1× density).
- Drawer interaction evidence: `artifacts/design-qa/mugensei-books-hamburger/implementation-drawer-open.png` (1440 × 900 CSS px at 1× density).
- Focused comparison: `artifacts/design-qa/mugensei-books-hamburger/comparison-header-before-after.png` (1488 × 500 px). The implementation header and context area was cropped to its top 126 CSS px and placed with the source in one comparison board; no density normalization was required.
- **[P2 · Information architecture, resolved]** The focused context strip duplicated library navigation through large Change book and Conversations buttons. Both controls were removed. A single hamburger beside the Mugensei logo now opens the existing library sheet, which includes the library, conversations, upload, and advanced settings.
- **[P2 · Spacing, resolved]** Removing the controls left excess vertical space. The read-only book/chapter strip was reduced from 80 px to 64 px, preserving hierarchy while returning space to the study workspace.
- **Fonts and typography:** Existing Geist UI labels and Source Serif study content are unchanged; the compact strip retains clear label/title/author hierarchy without new wrapping.
- **Colors and tokens:** The change reuses the existing Sumi, jade, border, and focus tokens; no new color drift or contrast regression was introduced.
- **Image and asset fidelity:** The existing Mugensei brand mark remains unchanged. The menu uses the shared Lucide icon family rather than custom or placeholder art.
- **Copy and content:** The strip is now descriptive only: book, author, and current chapter/path. Navigation language lives in the drawer where the actions occur.
- **Interactions and accessibility:** Browser checks confirmed that the named “Open the library panel” button expands the Library and settings dialog and exposes both Your library and Conversations. The standard desktop route keeps its persistent sidebar and does not render the hamburger there; compact layouts retain drawer access.
- **Browser errors:** A reload-time `pageerror` check reported no runtime errors.
- **Production verification:** Railway deployment `aea215d2-6d03-4914-b394-3817506035e2` succeeded, and `https://web-production-8529e.up.railway.app/` returned HTTP 200 after release.
- **Responsive note:** The current in-app browser session rendered at 1440 × 900 despite a temporary compact viewport override, so the existing responsive component tests remain the primary compact-breakpoint evidence for this follow-up.
- **Residual P3:** The deterministic preview has no persisted local library, so its open drawer shows the expected failed fixture request while still validating sheet behavior. This is not a production UI state.
- **Verification:** `npm run typecheck`, the focused 9-test AppShell/SplitPane suite, `npm run build`, and `git diff --check` all passed.

## Follow-up — global hamburger consistency

- Source visual truth: `artifacts/design-qa/mugensei-books-hamburger/implementation-focused.png`, using the approved Books header and hamburger placement.
- Browser-rendered implementations: `artifacts/design-qa/mugensei-global-menu/books-menu-open.png`, `papers-menu-open.png`, `videos-menu-open.png`, `interviews-menu-open.png`, and `cards-menu-open.png`.
- Full consistency comparison: `artifacts/design-qa/mugensei-global-menu/comparison-global-header-consistency.png` (1320 × 602 px), containing the approved source header and 1280 × 56 px focused crops from all five primary workspaces in one image.
- Viewport and normalization: implementations rendered at 1280 × 720 CSS px with device pixel ratio 2; browser screenshots were returned at 1280 × 720 px. The approved 1440 px source was cropped to the same 1280 × 56 header region before comparison. No scaling was applied.
- State: signed-in dark theme, primary authenticated route for Books, Papers, Videos, Interview, and Cards; each route's menu was captured open.
- **[P2 · Navigation consistency, resolved]** The hamburger previously disappeared on desktop layouts with a fixed sidebar and on routes without contextual rail content. `AppShell` now renders the same hamburger beside the logo whenever global navigation or contextual rail content exists, independent of route, breakpoint, document state, or rail mode.
- **[P2 · Menu information architecture, resolved]** The drawer previously exposed only route-specific rail content, leaving some screens without global navigation. Every drawer now begins with the same vertical Books, Papers, Videos, Interview, and Cards navigation, followed by optional screen-specific tools.
- **[P2 · Duplicate navigation, resolved]** Interview and Cards rails had embedded compact navigation. Those duplicates were removed because `AppShell` now owns the global menu consistently.
- **Fonts and typography:** All routes retain the approved Mugensei heading, Geist navigation typography, weights, line heights, and active-state hierarchy. Route-specific status copy remains intentionally contextual.
- **Spacing and layout rhythm:** The hamburger, brand mark, title, navigation, theme, and account controls retain the same 56 px header geometry across all five workspaces. Drawer navigation uses one-column rows with consistent padding and alignment.
- **Colors and visual tokens:** The shared Sumi background, green-gray divider, jade active state, and focus ring are unchanged across routes.
- **Image and asset fidelity:** The approved Mugensei mark is reused without substitution; Menu and workspace icons remain from the shared Lucide family.
- **Copy and content:** The generic accessible label is now “Open menu,” and the drawer description covers both global navigation and contextual tools rather than describing only the book library.
- **Interactions and accessibility:** Browser checks opened the named menu on all five routes and confirmed that every dialog contains Books, Papers, Videos, Interview, and Cards. The close control remained keyboard-named, and a reload-time `pageerror` check reported no runtime errors.
- **Responsive coverage:** The same trigger is no longer hidden by `lg:hidden` or drawer-only breakpoint rules. Component tests cover both contextual-rail and no-rail workspaces.
- **Verification:** `npm run typecheck`, the focused 10-test AppShell/SplitPane suite, `npm run build`, and `git diff --check` passed. The build emitted a non-fatal local cache-compaction shutdown warning after completing successfully; the generated build cache was removed and regenerated because the workstation volume was full.

final result: passed
