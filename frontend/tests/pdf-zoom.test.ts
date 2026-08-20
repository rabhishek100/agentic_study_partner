import { describe, expect, it } from "vitest";

import {
  MAX_PDF_ZOOM,
  MIN_PDF_ZOOM,
  zoomStep,
} from "@/components/pdf/zoom";

describe("pdf zoom", () => {
  it("goes further out than half", () => {
    // A tall page in a short window does not fit at 50%, which was the floor.
    expect(MIN_PDF_ZOOM).toBeLessThan(0.5);
  });

  it("steps finely below full size and coarsely above it", () => {
    // A quarter step at the bottom of the range halves the page in one press.
    expect(zoomStep(0.7, -1)).toBeCloseTo(0.55);
    expect(zoomStep(1.25, 1)).toBeCloseTo(1.5);
  });

  it("lands exactly on 100% rather than near it", () => {
    // The reset control reads the percentage back, and 0.9999 would not match.
    expect(zoomStep(0.85, 1)).toBe(1);
    expect(zoomStep(1.25, -1)).toBe(1);
  });

  it("walks the whole range without stalling", () => {
    let zoom = 1;
    for (let step = 0; step < 20; step += 1) {
      const next = zoomStep(zoom, -1);
      expect(next).toBeLessThan(zoom);
      zoom = Math.max(MIN_PDF_ZOOM, next);
      if (zoom === MIN_PDF_ZOOM) break;
    }
    expect(zoom).toBe(MIN_PDF_ZOOM);

    for (let step = 0; step < 20; step += 1) {
      const next = zoomStep(zoom, 1);
      expect(next).toBeGreaterThan(zoom);
      zoom = Math.min(MAX_PDF_ZOOM, next);
      if (zoom === MAX_PDF_ZOOM) break;
    }
    expect(zoom).toBe(MAX_PDF_ZOOM);
  });
});
