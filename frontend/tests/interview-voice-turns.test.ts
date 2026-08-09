import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  primeInterviewAudio,
  releasePrimedInterviewAudio,
} from "@/hooks/use-interview-voice";
import {
  primeInterviewerSpeech,
  useInterviewerSpeech,
} from "@/hooks/use-interviewer-speech";
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

  it("unlocks the device voice during the setup click", () => {
    const speak = vi.fn();
    class FakeUtterance {
      volume = 1;
      constructor(public text: string) {}
    }
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    vi.stubGlobal("speechSynthesis", { speak });

    primeInterviewerSpeech();

    expect(speak).toHaveBeenCalledOnce();
    expect(speak.mock.calls[0]?.[0]).toMatchObject({ text: " ", volume: 0 });
  });

  it("uses the device voice when hosted speech is unavailable", async () => {
    const speak = vi.fn((utterance: FakeUtterance) => {
      utterance.onstart?.();
      utterance.onend?.();
    });
    class FakeUtterance {
      voice: SpeechSynthesisVoice | null = null;
      volume = 1;
      rate = 1;
      pitch = 1;
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public text: string) {}
    }
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    vi.stubGlobal("speechSynthesis", {
      speak,
      cancel: vi.fn(),
      getVoices: () => [],
    });
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("provider timeout");
    }));
    const { result } = renderHook(() => useInterviewerSpeech());

    await act(() => result.current.speak("session", 0, "Explain the trade-off."));

    expect(speak).toHaveBeenCalledOnce();
    expect(speak.mock.calls[0]?.[0].text).toBe("Explain the trade-off.");
    expect(result.current.error).toBe("");
  });

  it("uses the settled turn for a spoken answer reaction", async () => {
    const speak = vi.fn((utterance: FakeUtterance) => {
      utterance.onstart?.();
      utterance.onend?.();
    });
    class FakeUtterance {
      voice: SpeechSynthesisVoice | null = null;
      volume = 1;
      rate = 1;
      pitch = 1;
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: (() => void) | null = null;
      constructor(public text: string) {}
    }
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    vi.stubGlobal("speechSynthesis", {
      speak,
      cancel: vi.fn(),
      getVoices: () => [],
    });
    const fetch = vi.fn(async (_url: string) => {
      throw new Error("provider timeout");
    });
    vi.stubGlobal("fetch", fetch);
    const { result } = renderHook(() => useInterviewerSpeech());

    await act(() =>
      result.current.speakReaction("session", 3, "That was well structured."),
    );

    expect(fetch.mock.calls[0]?.[0]).toContain(
      "/interviews/session/turns/3/reaction-speech",
    );
    expect(speak.mock.calls[0]?.[0].text).toBe("That was well structured.");
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
