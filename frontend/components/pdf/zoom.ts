/** The document pane's zoom range and stepping. Kept free of any pdf.js import.

The viewer cannot be loaded outside a browser — pdf.js reaches for `DOMMatrix`
at module scope — so anything worth testing lives here instead, exactly as
`target.ts` does for the pane's target.
*/

// A page rendered at a quarter of the pane is still legible enough to find
// your place by shape — a figure, a table, the start of a section — which is
// what zooming out is for. The floor was 0.5, which on a tall page in a short
// window was not far enough out to see a whole spread.
export const MIN_PDF_ZOOM = 0.25;
export const MAX_PDF_ZOOM = 2;
// Quarter steps everywhere would halve the page in one press at the bottom of
// the range, so the step narrows below 1: 0.25, 0.4, 0.55, 0.7, 0.85, 1.
export const PDF_ZOOM_STEP = 0.25;
const FINE_ZOOM_STEP = 0.15;

/** The step to take from here, in the given direction. */
export function zoomStep(zoom: number, direction: 1 | -1): number {
  const step = zoom <= 1 && (direction < 0 || zoom < 1) ? FINE_ZOOM_STEP : PDF_ZOOM_STEP;
  const next = zoom + direction * step;
  // Land exactly on 1 rather than passing near it: the reset control reads
  // 100% and a zoom of 0.9999 would not match it.
  if ((zoom < 1 && next > 1) || (zoom > 1 && next < 1)) return 1;
  return Math.round(next * 100) / 100;
}
