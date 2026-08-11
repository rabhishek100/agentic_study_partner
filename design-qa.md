# Deck question-navigator design QA

- Source visual truth: `/Users/abhishek/.codex/generated_images/019fee52-8fd9-73e0-a8cc-169fd60a1491/exec-1c53832a-dbfd-4cbe-9a53-43960919e855.png`
- Implementation screenshot: `/var/tmp/asp-deck-overview-qa.IghmcI/implementation-desktop-final.png`
- Same-input comparison: `/var/tmp/asp-deck-overview-qa.IghmcI/desktop-comparison.png`
- Responsive evidence: `/var/tmp/asp-deck-overview-qa.IghmcI/implementation-tablet-final.png`, `/var/tmp/asp-deck-overview-qa.IghmcI/implementation-mobile-final.png`, and `/var/tmp/asp-deck-overview-qa.IghmcI/implementation-mobile-detail.png`
- Matched viewport: 1672 × 941 CSS px, dark theme, first source exercise selected
- Pixel dimensions: source 1672 × 941; implementation 1672 × 941

## Result

No actionable P0, P1, or P2 findings remain.

The selected option's hierarchy is present and functional: compact deck masthead, source/coverage disclosure, chapter-exercise navigator, one selected reading pane, short grounded answer preview, source link, and a clear study action. The desktop divider lands within six pixels of the reference and the content starts at the same optical left edge.

## Intentional differences

- [P3] The established product shell is 56 px tall instead of the roomier generated header.
  - Evidence: the same-input comparison shows a denser global navigation band in the implementation.
  - Impact: minor fidelity difference only; changing the shared shell would affect Books, Papers, Videos, and Interview.
  - Resolution: retain the existing shell token for cross-product consistency.
- [P3] Navigator rows show the real source question verbatim instead of generated short labels such as `Flexible vs. inflexible methods`.
  - Evidence: the implementation uses the source-authored exercise text with a two-line clamp and full text in the accessible name/title.
  - Impact: rows are denser, but the UI does not invent or rewrite the book's questions.
  - Resolution: intentional grounding requirement; no fix.
- [P3] The implementation omits the generated mock's fake regeneration date and section label.
  - Evidence: the API does not currently provide a regeneration timestamp or canonical section display label.
  - Impact: provenance is shorter but truthful.
  - Resolution: render only available source and coverage data.
- [P3] A secondary `Detailed answer` control appears beside `Study this card`.
  - Evidence: the mock only previews the short answer; the production feature must also expose every full grounded answer.
  - Impact: one additional action, with preserved hierarchy because it is outlined and secondary.
  - Resolution: intentional functional requirement.

## Required fidelity surfaces

- Fonts and typography: Source Serif 4 remains the reading face and Geist remains the control face. The question, exercise label, short answer, and metadata reproduce the selected hierarchy without cramped or clipped text.
- Spacing and layout: the desktop navigator uses a responsive 34vw column, matching the reference divider at 1672 px. The detail article is left-aligned at the same optical margin instead of recentered in the remaining pane. Dividers replace the previous stack of oversized cards.
- Colors and tokens: the implementation uses the existing near-black background, low-contrast borders, mint primary, muted metadata, and accessible focus rings. There are no new gradients, generic card shadows, or decorative surfaces.
- Image quality and assets: the target contains no raster product assets. Existing brand and Lucide icons remain sharp and consistent; no CSS or hand-drawn substitutes were introduced.
- Copy and content: static labels are concise and task-oriented. Dynamic question, answer, citation, source, coverage, and schedule data remain API-driven and grounded.
- Icons: book, timer, chevrons, overflow menu, check, navigation, and source-link icons use the existing icon family and align to the surrounding text.
- States and interactions: question selection, All/Top filtering, provenance disclosure, detailed-answer disclosure, source opening, Start review, direct selected-card study, regenerate, and reset confirmation are wired. Browser checks confirmed the Top filter returns two high-priority fixture questions.
- Accessibility: semantic headings, nav/aside/main regions, `aria-current`, `aria-pressed`, labeled disclosures, keyboard-reachable controls, focus rings, and 44 px primary targets are present. Reset requires confirmation. Mobile selection uses a list → detail flow with an explicit `All exercises` return action.
- Responsiveness: browser checks at 1672 × 941, 768 × 1024, and 390 × 844 found no horizontal or document overflow. Tablet/mobile use a focused list-first master-detail flow instead of stacking two cramped panes.

## Comparison and fix history

1. Initial implementation — blocked by P2 desktop geometry and metadata noise.
   - The navigator was only 352 px wide, the reading pane was recentered too far right, and four repeated badges competed with the question.
   - Fix: changed the navigator to 34vw, left-aligned the 896 px reading article, and converted badges to one quiet metadata line.
2. First responsive pass — blocked by P2 mobile task flow.
   - The list and reading pane stacked into one long page, so tapping a question did not create a clear context change.
   - Fix: mobile/tablet now show the exercise list first, then a focused detail view with `All exercises` navigation.
3. Density pass — blocked by P2 action visibility.
   - The question's line length and the first detailed-answer disclosure pushed `Study this card` below the matched desktop viewport.
   - Fix: tuned the reading type size and moved `Detailed answer` beside the primary study action; both actions now appear above the fold.
4. Final comparison — passed.
   - Source and implementation were stacked in one 1672 px comparison input and inspected at original resolution.
   - Browser console check: no warnings or errors.
   - Responsive geometry: document `scrollWidth === clientWidth` and `scrollHeight === clientHeight` at all three checked viewports.

## Primary interactions tested

- Select a different source exercise and verify the full verbatim question and matching answer preview.
- Filter between All and Top questions.
- Expand provenance and coverage details.
- Expand the complete grounded answer.
- Open the deck overflow menu and verify regeneration/reset actions.
- Enter and exit the focused mobile question view.
- Confirm desktop, tablet, and mobile layouts have no horizontal overflow.

final result: passed
