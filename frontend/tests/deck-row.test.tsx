import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeckJobRow, DeckRow } from "@/components/decks/deck-row";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { DeckJob, DeckMetrics, DeckSummary } from "@/lib/deck-types";

const metrics: DeckMetrics = {
  topics_total: 4,
  topics_required: 4,
  topics_covered: 3,
  uncovered_topic_labels: ["One topic"],
  source_questions_total: 10,
  source_questions_covered: 10,
  uncovered_question_labels: [],
  cards_generated: 8,
  cards_kept: 7,
  cards_dropped_uncited: 0,
  cards_dropped_out_of_scope: 0,
  cards_dropped_duplicate: 1,
  cards_dropped_malformed: 0,
  cards_curated_out: 0,
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
  set_number: 1,
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
  title: "Chapter 2",
  source_title: "ISLP",
  created_at: "2026-08-11T10:00:00Z",
  updated_at: "2026-08-11T10:02:00Z",
  timing: {
    percent: 65,
    elapsed_seconds: 120,
    estimated_total_seconds: 180,
    estimated_remaining_seconds: 0,
    overrunning: false,
    stages: [
      {
        stage: "read_source",
        label: "Read source",
        state: "done",
        expected_seconds: 8,
        elapsed_seconds: null,
      },
      {
        stage: "generation",
        label: "Write grounded answers",
        state: "pending",
        expected_seconds: 120,
        elapsed_seconds: null,
      },
    ],
  },
};

describe("book-extracted deck presentation", () => {
  it("labels source questions and reports source-question coverage", () => {
    render(
      <TooltipProvider>
        <DeckRow deck={extractedDeck} />
      </TooltipProvider>,
    );
    expect(screen.getByText("From book")).toBeTruthy();
    expect(screen.getByText("100% covered")).toBeTruthy();
  });

  it("does not call a legacy extracted deck fully covered", () => {
    render(
      <TooltipProvider>
        <DeckRow
          deck={{
            ...extractedDeck,
            metrics: {
              ...metrics,
              source_questions_total: 0,
              source_questions_covered: 0,
            },
          }}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText("Needs regeneration")).toBeTruthy();
  });

  it("names extraction failures and lets the reader retry", () => {
    const retry = vi.fn();
    const dismiss = vi.fn();
    render(
      <DeckJobRow job={failedJob} onRetry={retry} onDismiss={dismiss} />,
    );
    expect(screen.getByText("We couldn’t extract these questions")).toBeTruthy();
    const details = screen.getByText("Technical details").closest("details");
    expect(details?.open).toBe(false);
    fireEvent.click(screen.getByText("Technical details"));
    expect(details?.open).toBe(true);
    expect(screen.getByText("Provider timed out")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(retry).toHaveBeenCalledWith(failedJob);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(dismiss).toHaveBeenCalledWith(failedJob);
  });
});

describe("numbered generated sets", () => {
  it("shows the successful set number in the deck row", () => {
    render(
      <TooltipProvider>
        <DeckRow
          deck={{
            ...extractedDeck,
            generation_mode: "topic_generated",
            set_number: 2,
          }}
        />
      </TooltipProvider>,
    );
    expect(screen.getByText("Set 2")).toBeTruthy();
  });
});
