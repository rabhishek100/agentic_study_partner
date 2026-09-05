import { describe, expect, it } from "vitest";

import { speakMath } from "@/lib/spoken-math";

describe("speaking maths", () => {
  it("says a fraction as a division", () => {
    expect(speakMath("\\frac{a}{b}")).toBe("a over b");
  });

  it("handles a fraction inside a fraction", () => {
    // A chain rule is not exotic notation, and a regex over flat braces
    // cannot see it.
    expect(speakMath("\\frac{\\frac{a}{b}}{c}")).toBe("a over b over c");
  });

  it("says powers as words rather than carets", () => {
    expect(speakMath("x^2")).toBe("x squared");
    expect(speakMath("x^3")).toBe("x cubed");
    expect(speakMath("A^T")).toBe("A transpose");
    expect(speakMath("x^{n+1}")).toBe("x to the power of n plus 1");
  });

  it("names Greek letters", () => {
    expect(speakMath("\\alpha + \\beta")).toBe("alpha plus beta");
  });

  it("reads a gradient the way it is said out loud", () => {
    expect(speakMath("\\nabla_w L")).toBe("the gradient of sub w L");
  });

  it("turns relations into words", () => {
    expect(speakMath("m \\approx \\sqrt{p}")).toBe(
      "m approximately the square root of p",
    );
    expect(speakMath("x \\leq y")).toBe("x is less than or equal to y");
  });

  it("drops grouping rather than reading brackets", () => {
    expect(speakMath("\\left( a + b \\right)")).toBe("a plus b");
  });

  it("keeps a hyphenated word intact", () => {
    // A hyphen between letters is a name far more often than a minus sign.
    expect(speakMath("\\text{cross-entropy}")).toBe("cross-entropy");
  });

  it("degrades an unknown command to its name, never to backslashes", () => {
    const spoken = speakMath("\\someunknownmacro{x}");
    expect(spoken).not.toContain("\\");
    expect(spoken).toContain("someunknownmacro");
  });
});
