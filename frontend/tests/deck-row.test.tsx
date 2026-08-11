import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeckJobRow, DeckRow } from "@/components/decks/deck-row";
import type { DeckJob, DeckMetrics, DeckSummary } from "@/lib/deck-types";

const metrics: DeckMetrics = {
  topics_total: 4,
  topics_required: 4,
  topics_covered: 3,
  uncovered_topic_labels: ["One topic"],
  cards_generated: 8,
  cards_kept: 7,
  cards_dropped_uncited: 0,
  cards_dropped_out_of_scope: 0,
  cards_dropped_duplicate: 1,
  cards_dropped_malformed: 0,
  cards_with_interview_angle: 0,
  card_type_counts: { qa: 7 },
  priority_counts: { "3": 7 },
  repair_attempted: false,
};

const extractedDeck: DeckSummary = {
  deck_id: "deck-1",
  source_kind: "book",
  generation_mode: "book_extracted",
  scope_key: "book:1:node:2:mode:book_extracted",
  version: 1,
  title: "Chapter 2",
  source_title: "ISLP",
  status: "ready",
  card_count: 7,
  topic_count: 4,
  book_id: 1,
  node_id: 2,
  video_id: null,
  metrics,
  due_count: 0,
  new_count: 7,
  updated_at: null,
};

const failedJob: DeckJob = {
  job_id: "job-1",
  source_kind: "book",
  generation_mode: "book_extracted",
  status: "failed",
  stage: "generation",
  scope_key: "book:1:node:2:mode:book_extracted",
  book_id: 1,
  node_id: 2,
  video_id: null,
  deck_id: null,
  topics_total: 3,
  topics_done: 2,
  progress: 0.67,
  attempt_count: 3,
  error_code: "generation_failed",
  error_detail: "Provider timed out",
};

describe("book-extracted deck presentation", () => {
  it("labels source questions without claiming AI topic coverage", () => {
    render(<DeckRow deck={extractedDeck} />);
    expect(screen.getByText("From book")).toBeTruthy();
    expect(screen.queryByText(/covered/)).toBeNull();
  });

  it("names extraction failures and lets the reader retry", () => {
    const retry = vi.fn();
    render(<DeckJobRow job={failedJob} onRetry={retry} />);
    expect(screen.getByText("Question extraction failed")).toBeTruthy();
    expect(screen.getByText("Provider timed out")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledWith(failedJob);
  });
});
