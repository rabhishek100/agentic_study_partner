import { describe, expect, it } from "vitest";

import { buildIdealTimeline, segmentAt, spokenSentences } from "@/lib/ideal-interview-timeline";
import type { IdealInterviewExchange } from "@/lib/interview-types";

const exchanges: IdealInterviewExchange[] = [{
  exchange_index: 0,
  phase: "requirements",
  topic_key: "requirements",
  topic_label: "Requirements",
  interviewer_text: "What would you clarify first?",
  candidate_text: "I'd clarify scale first. Then I'd define the latency target. Finally, I'd confirm durability.",
  citations: [{ marker: "[N1:P1]", node_id: 1, page: 1, evidence_rank: null, start_ms: null }],
  pause_after_question_ms: 650,
  pause_after_answer_ms: 900,
}];

describe("ideal interview timeline", () => {
  it("turns the whole interview into seekable sentence boundaries", () => {
    const timeline = buildIdealTimeline(exchanges);
    expect(timeline).toHaveLength(4);
    expect(timeline.map((item) => item.speaker)).toEqual([
      "interviewer", "candidate", "candidate", "candidate",
    ]);
    expect(segmentAt(timeline, timeline[2]!.start + 0.1)).toEqual(timeline[2]);
    expect(timeline.at(-1)!.end).toBeGreaterThan(timeline[0]!.end);
  });

  it("uses the same punctuation boundaries as the voice worker", () => {
    expect(spokenSentences("First decision. Then the trade-off? Finally, validate it.")).toEqual([
      "First decision.",
      "Then the trade-off?",
      "Finally, validate it.",
    ]);
  });
});
