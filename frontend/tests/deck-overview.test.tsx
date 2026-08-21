import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  DeckOverview,
  type DeckFilter,
} from "@/components/decks/deck-overview";
import { TooltipProvider } from "@/components/ui/tooltip";
import type {
  DeckDetailResponse,
  DeckMetrics,
  QueueCard,
} from "@/lib/deck-types";

function queueCard(index: number, front: string): QueueCard {
  return {
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
      card_id: `card-${index}`,
      topic_key: `exercise-${index}`,
      card_index: index - 1,
      card_type: "qa",
      front,
      back: {
        answer: `Detailed grounded answer ${index}.`,
        key_points: [],
        say_it_aloud: `Short answer ${index}.`,
        why_it_matters: "",
        options: [],
        components: [],
        data_flow: [],
        trade_offs: [],
        failure_modes: [],
      },
      citations: [
        {
          marker: `[N2:P${70 + index}]`,
          node_id: 2,
          page: 70 + index,
          evidence_rank: 1,
          start_ms: null,
          frame_id: null,
        },
      ],
      figures: [],
      interview_priority: index === 2 ? 5 : 3,
      priority_reason: "Useful",
      difficulty: "intermediate",
      interview_angle: null,
      answer_source: "rag_generated",
    },
  };
}

const metrics: DeckMetrics = {
  topics_total: 10,
  topics_required: 10,
  topics_covered: 10,
  uncovered_topic_labels: [],
  source_questions_total: 2,
  source_questions_covered: 2,
  uncovered_question_labels: [],
  cards_generated: 2,
  cards_kept: 2,
  cards_dropped_uncited: 0,
  cards_dropped_out_of_scope: 0,
  cards_dropped_duplicate: 0,
  cards_dropped_malformed: 0,
  cards_curated_out: 0,
  cards_with_interview_angle: 0,
  card_type_counts: { qa: 2 },
  priority_counts: { "3": 1, "5": 1 },
  repair_attempted: true,
};

const detail: DeckDetailResponse = {
  deck: {
    deck_id: "deck-1",
    source_kind: "book",
    generation_mode: "book_extracted",
    scope_key: "book:1:node:2:mode:book_extracted",
    version: 1,
    set_number: 1,
    title: "2 Statistical Learning",
    source_title: "ISLP",
    status: "ready",
    card_count: 2,
    topic_count: 2,
    book_id: 1,
    node_id: 2,
    video_id: null,
    metrics,
    due_count: 2,
    new_count: 2,
    updated_at: null,
  },
  cards: [
    queueCard(1, "First source-authored exercise question."),
    queueCard(2, "Second source-authored exercise question."),
  ],
};

function renderOverview(
  overrides: Partial<React.ComponentProps<typeof DeckOverview>> = {},
) {
  const props = {
    detail,
    dueNow: 2,
    reviewableCardIds: ["card-1", "card-2"],
    filter: "all" as DeckFilter,
    generatingSet: false,
    onFilterChange: vi.fn(),
    onStartReview: vi.fn(),
    onStudyCard: vi.fn(),
    onGenerateSet: vi.fn(),
    onReset: vi.fn(),
    onOpenSource: vi.fn(),
    ...overrides,
  };
  render(
    <TooltipProvider>
      <DeckOverview {...props} />
    </TooltipProvider>,
  );
  return props;
}

describe("deck question navigator", () => {
  it("switches the reading pane without rewriting the source question", () => {
    const props = renderOverview();

    expect(
      screen.getByRole("heading", {
        name: "First source-authored exercise question.",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Short answer 1.")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", {
        name: /Second source-authored exercise question/,
      }),
    );

    expect(
      screen.getByRole("heading", {
        name: "Second source-authored exercise question.",
      }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Study this card" }));
    expect(props.onStudyCard).toHaveBeenCalledWith(detail.cards[1]);
  });

  it("keeps provenance visible but moves generation details on demand", () => {
    renderOverview();

    expect(screen.getByText("Source: ISLP")).toBeInTheDocument();
    expect(screen.queryByText("Dropped as duplicate")).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Provenance & coverage" }),
    );

    expect(screen.getByText("Questions found")).toBeInTheDocument();
    expect(screen.getByText("Dropped as duplicate")).toBeInTheDocument();
    expect(screen.getByText("Used")).toBeInTheDocument();
  });

  it("reveals the complete grounded answer below the short preview", () => {
    renderOverview();

    expect(screen.queryByText("Detailed grounded answer 1.")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Detailed answer" }),
    );
    expect(screen.getByText("Detailed grounded answer 1.")).toBeInTheDocument();
  });

  it("does not start review on a card outside today's queue", () => {
    renderOverview({ reviewableCardIds: ["card-1"] });
    fireEvent.click(
      screen.getByRole("button", {
        name: /Second source-authored exercise question/,
      }),
    );

    expect(screen.getByRole("button", { name: "Not due today" })).toBeDisabled();
  });
});

describe("numbered generated set actions", () => {
  it("offers the next set without replacing the current one", async () => {
    const onGenerateSet = vi.fn();
    renderOverview({
      detail: {
        ...detail,
        deck: {
          ...detail.deck,
          generation_mode: "topic_generated",
          set_number: 2,
        },
      },
      onGenerateSet,
    });

    expect(screen.getByText("Set 2")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole("button", { name: "Deck options" }), {
      button: 0,
      ctrlKey: false,
    });
    fireEvent.click(await screen.findByText("Create Set 3"));
    expect(onGenerateSet).toHaveBeenCalledOnce();
  });
});
