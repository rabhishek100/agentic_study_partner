import { afterEach, describe, expect, it, vi } from "vitest";

import {
  primeInterviewAudio,
  releasePrimedInterviewAudio,
} from "@/hooks/use-interview-voice";
import { appendTranscriptSegment } from "@/lib/interview-types";

afterEach(() => {
  releasePrimedInterviewAudio();
  vi.unstubAllGlobals();
});

describe("interview audio activation", () => {
  it("resumes Web Audio during the setup click", () => {
    const resume = vi.fn(async () => undefined);
    const close = vi.fn(async () => undefined);
    class FakeAudioContext {
      state: AudioContextState = "suspended";
      resume = resume;
      close = close;
    }
    vi.stubGlobal("AudioContext", FakeAudioContext);

    primeInterviewAudio();

    expect(resume).toHaveBeenCalledOnce();
    releasePrimedInterviewAudio();
    expect(close).toHaveBeenCalledOnce();
  });
});

describe("continuous interview transcription", () => {
  it("keeps thinking pauses as separate draft segments", () => {
    const first = appendTranscriptSegment("", "I would begin with requirements.");
    const complete = appendTranscriptSegment(
      first,
      "Then I would estimate traffic before choosing storage.",
    );

    expect(complete).toBe(
      "I would begin with requirements. Then I would estimate traffic before choosing storage.",
    );
  });

  it("preserves typed corrections when more speech arrives", () => {
    expect(
      appendTranscriptSegment(
        "I would use consistent hashing, not ordinary hashing.",
        "That limits the keys moved when a node changes.",
      ),
    ).toContain("not ordinary hashing");
  });

  it("ignores empty silence segments", () => {
    expect(appendTranscriptSegment("Existing draft", "   ")).toBe(
      "Existing draft",
    );
  });
});
