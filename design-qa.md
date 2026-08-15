# Design QA — Mugensei Videos, Option 3

## Scope

- Selected source: `artifacts/design-qa/mugensei-videos-option-3/source-option-3.png`
- Implemented route: `/videos`
- Deterministic local QA state: `/videos?design-preview=videos` (development only)
- Desktop viewport: 1440 × 1024 CSS px at 1× density
- Mobile viewport: 390 × 844 CSS px at 1× density
- Reference source normalized from 1487 × 1058 to 1440 × 1024 for comparison. The aspect-ratio difference is under 0.1%.

## Evidence

- Final full desktop comparison (source left, implementation right): `artifacts/design-qa/mugensei-videos-option-3/comparison-desktop-final.png`
- Final focused feature comparison (source left, implementation right): `artifacts/design-qa/mugensei-videos-option-3/comparison-feature-focused.png`
- Final desktop implementation: `artifacts/design-qa/mugensei-videos-option-3/implementation-desktop-final.png`
- Mobile first viewport: `artifacts/design-qa/mugensei-videos-option-3/implementation-mobile-viewport.png`
- Mobile expanded import flow: `artifacts/design-qa/mugensei-videos-option-3/implementation-mobile-import.png`
- Verified signed-in production: `artifacts/design-qa/mugensei-videos-option-3/production-final.png`

## Comparison history

### Pass 1 — blocked

- **[P1 · Color/state]** The browser had persisted the light theme, so the first screenshot did not exercise the selected dark direction. Switched the controlled QA state to explicit dark mode and recaptured.
- **[P2 · Layout/fidelity]** The first featured image used a 16:9 thumbnail, making the central card much shorter than the source and weakening the lecture-first hierarchy. Changed the image slot to 4:3 and rebalanced the two-column grid.
- **[P2 · Evidence path]** The first implementation used three divided columns instead of a visible transcript → screen → slides sequence. Replaced the dividers with solid and dashed connectors and kept the optional slide state in vermilion.
- **[P2 · Content order]** Metadata appeared above the primary action. Reordered the title, CTA, date metadata, and evidence path to match the selected composition.
- **[P2 · Real-data imagery]** The first live production check selected an opening Stanford title card, which became an oversized cropped logo. The representative-frame selector now skips title, branding, logo, and intro frames when a teaching frame exists; a regression test covers this case.

### Final pass — passed

- **Typography:** Mugensei's existing serif heading and sans-serif UI pairing preserves the source hierarchy. Long lecture filenames wrap safely on mobile.
- **Layout and spacing:** Desktop maintains the left library rail, wide featured lecture card, tall visual, paired action/evidence column, and compact import module. Mobile collapses to one column without horizontal overflow or clipped controls.
- **Colors and tokens:** Near-black sumi surfaces, jade actions/status, warm bone text, and rare vermilion attention states match the selected direction. Measured dark-theme contrast ratios: foreground/background 17.08:1, muted/background 10.58:1, primary/background 10.76:1, primary-foreground/primary 10.42:1, and seal/background 6.85:1.
- **Image quality:** The QA fixture uses a generated 1152 × 648 lecture still sized for the slot. Production uses the lecture's authenticated indexed frame. The implementation intentionally does not fake playback chrome on a non-player library card.
- **Icons:** Existing Lucide icons provide one consistent stroke family for navigation, evidence states, actions, and import controls.
- **Copy:** The page and import language consistently describes grounded transcript, screen, and optional slide evidence.
- **States and interactions:** Verified the dark-theme control, featured CTA, overflow action, collapsible import module, YouTube/video-file source switch, and no-slides/PDF-link/PDF-file choices. The submit control stays disabled until required source input exists.
- **Production data:** Verified the signed-in live lecture with a representative authenticated teaching frame, 258 indexed screen frames, and 135 slide pages.
- **Accessibility:** Controls have names or associated labels, pressed states are exposed, focus styling inherits the high-contrast jade ring, mobile targets remain practical, and the existing reduced-motion-aware design system remains in force.
- **Responsive behavior:** Verified at 1440 × 1024 and 390 × 844. No content overlap, horizontal clipping, or unusable controls was observed. The black circular “N” visible in local mobile evidence is the Next.js development overlay and is absent from production.

## Verification

- `npm run typecheck` — passed
- `npx vitest run tests/video-feature-card.test.tsx tests/video-card.test.tsx` — 12 tests passed
- `npm run build` — passed

final result: passed
