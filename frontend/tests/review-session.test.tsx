import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { structuredAnswer } from "@/components/decks/card-face";
import { ReviewSession } from "@/components/decks/review-session";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { QueueCard } from "@/lib/deck-types";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const item: QueueCard = {
  deck_id: "deck-1",
  deck_title: "2 Statistical Learning",
  source_kind: "book",
  source_title: "ISLP",
  book_id: 1,
  video_id: null,
  review: {
    state: "new",
    due_at: null,
    interval_days: 0,
    ease: 2.5,
    reps: 0,
    lapses: 0,
    last_reviewed_at: null,
    last_rating: null,
  },
  card: {
    card_id: "card-1",
    topic_key: "exercise-1",
    card_index: 0,
    card_type: "qa",
    front:
      "1. For each of parts (a) and (b), compare flexible and inflexible methods.\n(a) Large n and small p.\n(b) Large p and small n.",
    back: {
      answer:
        "(a) Flexible is generally better because the large sample controls variance. (b) Flexible is generally worse because the small sample encourages overfitting.",
      key_points: ["Large sample, few predictors", "Small sample, many predictors"],
      say_it_aloud:
        "Flexibility helps with enough data and hurts when predictors overwhelm the sample.",
      why_it_matters: "",
      options: [],
      components: [],
      data_flow: [],
      trade_offs: [],
      failure_modes: [],
    },
    citations: [
      {
        marker: "[N42:P73]",
        node_id: 42,
        page: 73,
        evidence_rank: null,
        start_ms: null,
        frame_id: null,
      },
    ],
    figures: [],
    interview_priority: 3,
    priority_reason: "Foundational trade-off",
    difficulty: "intermediate",
    interview_angle: null,
    answer_source: "rag_generated",
  },
};

function renderSession(
  overrides: Partial<React.ComponentProps<typeof ReviewSession>> = {},
) {
  return render(
    <TooltipProvider>
      <ReviewSession cards={[item]} sourceQuestions {...overrides} />
    </TooltipProvider>,
  );
}

describe("focused deck review", () => {
  it("keeps the session navigation and answer action in a focused reading view", () => {
    const onExit = vi.fn();
    renderSession({ onExit });

    expect(screen.getByText("2 Statistical Learning")).toBeInTheDocument();
    expect(screen.getByText("Exercise 1")).toBeInTheDocument();
    expect(screen.getByText(/For each of parts/)).toBeInTheDocument();
    expect(screen.queryByText("30-second answer")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Back to deck" }));
    expect(onExit).toHaveBeenCalledOnce();
  });

  it("reveals a short answer, structured subparts, and pinned rating choices", () => {
    renderSession();

    fireEvent.click(screen.getByRole("button", { name: /Show answer/ }));

    expect(screen.getByText("30-second answer")).toBeInTheDocument();
    expect(screen.getByText("Large sample, few predictors")).toBeInTheDocument();
    expect(screen.getByText("Small sample, many predictors")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Again/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Hard/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Good/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Easy/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open source" })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Question (collapse)" }),
    );
    expect(screen.queryByText(/For each of parts/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Question (expand)" }),
    ).toBeInTheDocument();
  });
});

describe("structuredAnswer", () => {
  it("uses existing labels and key points without rewriting the answer", () => {
    const sections = structuredAnswer(
      item.card.back.answer,
      item.card.back.key_points,
    );

    expect(sections).toEqual([
      {
        label: "(a)",
        title: "Large sample, few predictors",
        body: "Flexible is generally better because the large sample controls variance.",
      },
      {
        label: "(b)",
        title: "Small sample, many predictors",
        body: "Flexible is generally worse because the small sample encourages overfitting.",
      },
    ]);
  });
});
