import { describe, expect, it } from "vitest";

import {
  microphoneProblem,
  recordingClock,
  spliceTranscript,
} from "@/lib/dictation";

describe("spliceTranscript", () => {
  it("puts spoken words at the caret, spaced the way typing would be", () => {
    const spliced = spliceTranscript("How does work?", "backpropagation", 9);

    expect(spliced.value).toBe("How does backpropagation work?");
    // The caret follows the words, so the next thing said continues them.
    expect(spliced.caret).toBe("How does backpropagation".length);
  });

  it("adds no spacing an author did not ask for", () => {
    expect(spliceTranscript("", "What is LoRA?", 0)).toEqual({
      value: "What is LoRA?",
      caret: 13,
    });
    expect(spliceTranscript("Explain ", "attention", 8).value).toBe(
      "Explain attention",
    );
  });

  it("replaces a selection, exactly as typing over it would", () => {
    const spliced = spliceTranscript("Explain gradients here", "vectors", 8, 17);

    expect(spliced.value).toBe("Explain vectors here");
    expect(spliced.caret).toBe("Explain vectors".length);
  });

  it("trims the transcript so a stray edge space cannot double up", () => {
    expect(spliceTranscript("Explain", "  attention  ", 7).value).toBe(
      "Explain attention",
    );
  });

  it("leaves the question untouched when nothing was said", () => {
    expect(spliceTranscript("Explain LoRA", "   ", 4)).toEqual({
      value: "Explain LoRA",
      caret: 4,
    });
  });

  it("clamps a caret that no longer fits the value", () => {
    const spliced = spliceTranscript("Hi", "there", 99, 99);

    expect(spliced.value).toBe("Hi there");
    expect(spliced.caret).toBe(8);
  });
});

describe("recordingClock", () => {
  it("reads as mm:ss", () => {
    expect(recordingClock(0)).toBe("0:00");
    expect(recordingClock(9_400)).toBe("0:09");
    expect(recordingClock(75_000)).toBe("1:15");
  });
});

describe("microphoneProblem", () => {
  it("names the fix for each way a microphone refuses", () => {
    expect(microphoneProblem(new DOMException("", "NotAllowedError"))).toMatch(
      /browser settings/,
    );
    expect(microphoneProblem(new DOMException("", "NotFoundError"))).toMatch(
      /No microphone/,
    );
    expect(microphoneProblem(new DOMException("", "NotReadableError"))).toMatch(
      /another app/,
    );
    expect(microphoneProblem(new Error("who knows"))).toMatch(
      /could not be started/,
    );
  });
});
