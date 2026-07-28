import { describe, expect, it } from "vitest";

import {
  findExcerptRange,
  normalizeForMatch,
  similarity,
} from "@/lib/pdf-match";

const items = (...strings: string[]) => strings.map((str) => ({ str }));

describe("normalizeForMatch", () => {
  it("folds ligatures", () => {
    // pdf.js emits the ligature; the stored excerpt has plain letters.
    expect(normalizeForMatch("the ﬁrst coefﬁcient")).toBe(
      normalizeForMatch("the first coefficient"),
    );
  });

  it("rejoins a word broken across a line end", () => {
    expect(normalizeForMatch("regres-\nsion")).toBe("regression");
  });

  it("drops soft hyphens", () => {
    expect(normalizeForMatch("regres­sion")).toBe("regression");
  });

  it("treats every dash variant alike", () => {
    expect(normalizeForMatch("least–squares")).toBe("least squares");
  });

  it("collapses layout whitespace and punctuation", () => {
    expect(normalizeForMatch("  Sales,   in   thousands. ")).toBe(
      "sales in thousands",
    );
  });

  it("returns empty for text with nothing to match on", () => {
    expect(normalizeForMatch("  —  ")).toBe("");
  });
});

describe("similarity", () => {
  it("scores an exact match as 1", () => {
    expect(similarity("least squares", "least squares")).toBe(1);
  });

  it("scores disjoint text as 0", () => {
    expect(similarity("least squares", "gradient boosting")).toBe(0);
  });

  it("is not penalised by surrounding text", () => {
    // The page holds the excerpt plus a great deal more.
    expect(
      similarity("least squares", "we now discuss least squares in detail"),
    ).toBe(1);
  });

  it("does not double-count a repeated word", () => {
    expect(similarity("the the the", "the")).toBeCloseTo(1 / 3);
  });

  it("handles empty input", () => {
    expect(similarity("", "anything")).toBe(0);
  });
});

describe("findExcerptRange", () => {
  it("finds an excerpt spanning several text runs", () => {
    const page = items(
      "3.1.1 Estimating the Coefficients",
      "In practice, ",
      "the coefficients are unknown ",
      "and must be estimated.",
      "Figure 3.1 displays the fit.",
    );

    const match = findExcerptRange(
      page,
      "the coefficients are unknown and must be estimated",
    );

    expect(match).not.toBeNull();
    expect(match!.start).toBe(2);
    expect(match!.end).toBe(3);
  });

  it("matches despite a hyphenated line break in the page", () => {
    const page = items("we estimate by minimizing the residual sum of squa-\nres");
    const match = findExcerptRange(
      page,
      "minimizing the residual sum of squares",
    );
    expect(match).not.toBeNull();
  });

  it("returns null when the passage is not on the page", () => {
    // The fallback signal: highlight the page, not the wrong paragraph.
    const page = items("An entirely different chapter about clustering.");
    expect(
      findExcerptRange(page, "the coefficients are unknown and estimated"),
    ).toBeNull();
  });

  it("returns null for an empty excerpt", () => {
    expect(findExcerptRange(items("anything"), "   ")).toBeNull();
  });

  it("returns null for an empty page", () => {
    expect(findExcerptRange([], "least squares")).toBeNull();
  });

  it("prefers the closer of two similar passages", () => {
    const page = items(
      "least squares is mentioned here briefly",
      "the least squares method estimates the coefficients by minimizing RSS",
    );

    const match = findExcerptRange(
      page,
      "the least squares method estimates the coefficients by minimizing RSS",
    );

    expect(match!.start).toBe(1);
    expect(match!.score).toBe(1);
  });

  it("honours a stricter threshold", () => {
    const page = items("least squares appears but little else matches");
    expect(
      findExcerptRange(page, "least squares method estimates coefficients", 0.99),
    ).toBeNull();
  });

  it("ignores empty runs between the words it needs", () => {
    const page = items("the coefficients", "   ", "are unknown");
    const match = findExcerptRange(page, "the coefficients are unknown");
    expect(match).not.toBeNull();
    expect(match!.end).toBe(2);
  });
});

describe("findExcerptRange tightness", () => {
  it("prefers the tightest window when several contain the excerpt", () => {
    // Windows starting at 0 and at 1 both contain every excerpt word; the
    // one that starts at the excerpt is the right highlight.
    const page = items(
      "Preamble text. ",
      "the coefficients are unknown ",
      "and must be estimated.",
    );

    const match = findExcerptRange(
      page,
      "the coefficients are unknown and must be estimated",
    );

    expect(match!.start).toBe(1);
    expect(match!.end).toBe(2);
  });
});
