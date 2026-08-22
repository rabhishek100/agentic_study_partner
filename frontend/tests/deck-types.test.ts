import { describe, expect, it } from "vitest";

import {
  type DeckJob,
  type DeckMetrics,
  type DeckSummary,
  coveragePercent,
  describeInterval,
  jobIsLive,
  nextSetRequest,
} from "@/lib/deck-types";

const metrics = (overrides: Partial<DeckMetrics> = {}): DeckMetrics => ({
  topics_total: 0,
  topics_required: 0,
  topics_covered: 0,
  uncovered_topic_labels: [],
  source_questions_total: 0,
  source_questions_covered: 0,
  uncovered_question_labels: [],
  source_items_total: 0,
  source_items_covered: 0,
  source_item_kind_counts: {},
  source_item_placement_counts: {},
  uncovered_source_items: [],
  cards_generated: 0,
  cards_kept: 0,
  cards_dropped_uncited: 0,
  cards_dropped_out_of_scope: 0,
  cards_dropped_duplicate: 0,
  cards_dropped_malformed: 0,
  cards_curated_out: 0,
  cards_with_interview_angle: 0,
  card_type_counts: {},
  priority_counts: {},
  repair_attempted: false,
  ...overrides,
});

describe("describeInterval", () => {
  it("uses minutes and hours below a day", () => {
    expect(describeInterval(10 / 1440)).toBe("10 min");
    expect(describeInterval(3 / 24)).toBe("3 h");
  });

  it("keeps a decimal for short day-counts", () => {
    // The whole point: these two sit on adjacent grading buttons and must not
    // both read "3 d".
    expect(describeInterval(2.5)).toBe("2.5 d");
    expect(describeInterval(3.25)).toBe("3.3 d");
  });

  it("drops a pointless decimal", () => {
    expect(describeInterval(1)).toBe("1 d");
    expect(describeInterval(4)).toBe("4 d");
  });

  it("rounds once the gap is long enough not to compare", () => {
    expect(describeInterval(12.4)).toBe("12 d");
    expect(describeInterval(60)).toBe("2 mo");
    expect(describeInterval(365)).toBe("1.0 y");
  });

  it("says now rather than zero", () => {
    expect(describeInterval(0)).toBe("now");
    expect(describeInterval(-1)).toBe("now");
  });
});

describe("coveragePercent", () => {
  it("reports the share of required topics that got a card", () => {
    expect(
      coveragePercent(metrics({ topics_required: 27, topics_covered: 25 })),
    ).toBe(93);
  });

  it("treats a scope with nothing required as covered", () => {
    expect(coveragePercent(metrics())).toBe(100);
  });

  it("uses unified source-item coverage for exercises and worked examples", () => {
    expect(
      coveragePercent(
        metrics({
          source_questions_total: 10,
          source_questions_covered: 10,
          source_items_total: 5,
          source_items_covered: 4,
        }),
      ),
    ).toBe(80);
  });
});

describe("jobIsLive", () => {
  const job = (status: DeckJob["status"]): DeckJob => ({
    job_id: "j",
    source_kind: "book",
    status,
    stage: "generation",
    scope_key: "book:1:node:2",
    book_id: 1,
    node_id: 2,
    video_id: null,
    deck_id: null,
    topics_total: 4,
    topics_done: 1,
    progress: 0.25,
    attempt_count: 1,
    error_code: null,
    error_detail: null,
    title: "Chapter 2",
    source_title: "ISLP",
    created_at: null,
    updated_at: null,
    timing: {
      percent: 0,
      elapsed_seconds: 0,
      estimated_total_seconds: 120,
      estimated_remaining_seconds: 120,
      overrunning: false,
      stages: [],
    },
  });

  it("counts queued and running as work in flight", () => {
    expect(jobIsLive(job("queued"))).toBe(true);
    expect(jobIsLive(job("running"))).toBe(true);
  });

  it("counts every settled status as finished", () => {
    expect(jobIsLive(job("succeeded"))).toBe(false);
    expect(jobIsLive(job("failed"))).toBe(false);
    expect(jobIsLive(job("cancelled"))).toBe(false);
  });
});

describe("nextSetRequest", () => {
  it("keeps a whole paper as one null-node scope for the next set", () => {
    const paper = {
      source_kind: "book",
      document_type: "paper",
      generation_mode: "topic_generated",
      book_id: 42,
      node_id: null,
      video_id: null,
    } as DeckSummary;

    expect(nextSetRequest(paper)).toEqual({
      source_kind: "book",
      generation_mode: "topic_generated",
      book_id: 42,
      node_id: null,
    });
  });

  it("still rejects an invalid null-node book scope", () => {
    const book = {
      source_kind: "book",
      document_type: "book",
      generation_mode: "topic_generated",
      book_id: 42,
      node_id: null,
      video_id: null,
    } as DeckSummary;

    expect(nextSetRequest(book)).toBeNull();
  });
});
