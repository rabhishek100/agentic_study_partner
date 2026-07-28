import { describe, expect, it } from "vitest";

import { normalizeMath } from "@/lib/math";

describe("normalizeMath", () => {
  it("converts inline \\( \\) delimiters to single dollars", () => {
    expect(normalizeMath("typically \\(m \\approx \\sqrt{p}\\) is chosen")).toBe(
      "typically $m \\approx \\sqrt{p}$ is chosen",
    );
  });

  it("converts display \\[ \\] delimiters to a $$ block", () => {
    // The delimiters must stand alone: `remark-math` only produces display
    // maths for a block, and renders inline `$$…$$` as cramped inline maths.
    expect(normalizeMath("\\[ \\hat{y} = X\\beta \\]")).toBe(
      "\n\n$$\n\\hat{y} = X\\beta\n$$\n\n",
    );
  });

  it("handles several expressions in one paragraph", () => {
    expect(normalizeMath("\\(a\\) and \\(b\\) differ")).toBe(
      "$a$ and $b$ differ",
    );
  });

  it("spans newlines inside display math", () => {
    expect(normalizeMath("\\[\na + b\n\\]")).toBe("\n\n$$\na + b\n$$\n\n");
  });

  it("leaves existing dollar math untouched", () => {
    expect(normalizeMath("already $x^2$ here")).toBe("already $x^2$ here");
  });

  it("leaves inline code spans alone", () => {
    expect(normalizeMath("write `\\(x\\)` to get math")).toBe(
      "write `\\(x\\)` to get math",
    );
  });

  it("leaves fenced code blocks alone", () => {
    const source = "Example:\n\n```tex\n\\(x + y\\)\n```\n";
    expect(normalizeMath(source)).toBe(source);
  });

  it("converts math outside a fence while preserving the fence", () => {
    const result = normalizeMath("\\(a\\)\n\n```\n\\(b\\)\n```\n\\(c\\)");
    expect(result).toBe("$a$\n\n```\n\\(b\\)\n```\n$c$");
  });

  it("returns text without math unchanged", () => {
    expect(normalizeMath("no math at all")).toBe("no math at all");
  });

  it("leaves an unclosed delimiter alone rather than corrupting the text", () => {
    expect(normalizeMath("open \\(m here")).toBe("open \\(m here");
  });
});
