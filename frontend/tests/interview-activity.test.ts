import { describe, expect, it } from "vitest";

import {
  describeInterviewActivity,
  type InterviewActivityInput,
} from "@/lib/interview-activity";

const base: InterviewActivityInput = {
  operation: "idle",
  interviewStatus: "active",
  transitionInProgress: false,
  speechLoading: false,
  speechSpeaking: false,
  voiceStatus: "idle",
  listeningPaused: false,
  hasCurrentQuestion: true,
};

describe("interview processing status", () => {
  it("names every server-side answer task without pretending to know a substep", () => {
    const activity = describeInterviewActivity({
      ...base,
      operation: "submitting_answer",
    });

    expect(activity.title).toBe("Evaluating your answer");
    expect(activity.stages).toEqual([
      "Source grounding",
      "Answer evaluation",
      "Next-turn planning",
    ]);
  });

  it("distinguishes transcription from microphone capture", () => {
    expect(
      describeInterviewActivity({ ...base, voiceStatus: "processing" }).title,
    ).toBe("Transcribing your latest speech");
    expect(
      describeInterviewActivity({ ...base, voiceStatus: "recording" }).title,
    ).toBe("Capturing your answer");
  });

  it("makes submission recovery explicit", () => {
    const activity = describeInterviewActivity({
      ...base,
      operation: "checking_submission",
    });

    expect(activity.title).toBe("Confirming whether your answer was saved");
    expect(activity.detail).toContain("before asking you to retry");
  });

  it("distinguishes feedback audio from question audio", () => {
    expect(
      describeInterviewActivity({
        ...base,
        transitionInProgress: true,
        speechLoading: true,
      }).title,
    ).toBe("Preparing spoken feedback");
    expect(
      describeInterviewActivity({ ...base, speechLoading: true }).title,
    ).toBe("Preparing question audio");
  });

  it("gives screen analysis priority over passive voice state", () => {
    expect(
      describeInterviewActivity({
        ...base,
        operation: "screen_checkpoint",
        voiceStatus: "listening",
      }).title,
    ).toBe("Analyzing your screen checkpoint");
  });
});
