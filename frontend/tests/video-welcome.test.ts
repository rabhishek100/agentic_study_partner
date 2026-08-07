import { describe, expect, it } from "vitest";

import { videoStarters } from "@/components/video/video-welcome";
import type { VideoChapter } from "@/lib/video-types";

function chapters(...titles: string[]): VideoChapter[] {
  return titles.map((title, index) => ({
    chapter_index: index,
    chapter_kind: "derived",
    title,
    start_ms: index * 60_000,
    end_ms: (index + 1) * 60_000,
  }));
}

describe("videoStarters", () => {
  it("takes its chapter starter from the middle of the lecture", () => {
    // A recording opens on a title card and branding and closes on a wrap-up.
    // "Explain Stanford ENGINEERING" is a starter that teaches a reader the
    // feature does not work.
    const starters = videoStarters(
      chapters(
        "Stanford ENGINEERING",
        "Welcome to CME 295",
        "Tokenization",
        "Attention mechanism",
        "Thank you for your attention",
      ),
    );

    expect(starters).toContain("Explain Tokenization");
    expect(starters).not.toContain("Explain Stanford ENGINEERING");
  });

  it("offers the whole-lecture requests first", () => {
    const starters = videoStarters(chapters("Tokenization", "Attention"));
    expect(starters[0]).toBe("Summarize this lecture");
    expect(starters).toHaveLength(3);
  });

  it("still gives a lecture with no chapters something to ask", () => {
    const starters = videoStarters([]);
    expect(starters).toHaveLength(3);
    expect(starters.every((starter) => starter.length > 0)).toBe(true);
  });
});
