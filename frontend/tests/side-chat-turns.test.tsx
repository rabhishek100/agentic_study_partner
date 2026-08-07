import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  AnchorChips,
  SideChatTurns,
} from "@/components/side-chat/side-chat-turns";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ChatTurn, EvidenceRef, TurnResult } from "@/lib/types";

const evidence: EvidenceRef = {
  node_id: 42,
  pages: [111, 112],
  path: "8 Advanced Practice :: 8.3 Training Neural Networks",
  book_id: 1,
  book_title: "The Hundred-Page Machine Learning Book",
  rank: 1,
  chunk_id: "chunk-one",
  chunk_index: 0,
  retrieval_method: "anchor_pin",
  score: 1,
  excerpt: "Images are resized to common dimensions.",
};

function turn(overrides: Partial<ChatTurn> = {}): ChatTurn {
  const result: TurnResult = {
    question: "How do you detect this in production?",
    answer: "Verify the transformations match [S1].",
    route: "retrieval_qa",
    history_dependency: "dependent",
    standalone_query: "How is training-serving skew detected?",
    resolved_scope: null,
    evidence: [evidence],
    citations: [
      {
        marker: "[S1]",
        node_id: 42,
        page: 111,
        book_id: 1,
        evidence_rank: 1,
      },
    ],
    figures: [],
    outline_node_ids: [],
    outcome: "answer",
    retrieval_mode: "hybrid_rerank",
    warnings: [],
    answer_archetype: "concept_explanation",
    response_depth: "quick",
    routing_reason: "question about a highlighted passage",
    prompt_profile_version: "interview-v2",
    side_context: {
      anchor_ids: ["anchor-one"],
      pinned_chunk_ids: ["chunk-one"],
      token_count: 1332,
      token_budget: 1800,
      dropped: [],
    },
  };
  return {
    id: "turn-1",
    question: result.question,
    answer: result.answer,
    status: "complete",
    result,
    error: null,
    turnIndex: 0,
    ...overrides,
  };
}

describe("SideChatTurns", () => {
  it("opens a cited page in the reading pane, like the main conversation does", () => {
    const onOpenReference = vi.fn();
    render(
      <TooltipProvider>
        <SideChatTurns
          turns={[turn()]}
          isLoading={false}
          onOpenReference={onOpenReference}
        />
      </TooltipProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: /open/i }));

    expect(onOpenReference).toHaveBeenCalledWith(
      expect.objectContaining({ chunk_id: "chunk-one" }),
    );
  });

  it("shows the anchored context report for the turn", () => {
    render(
      <TooltipProvider>
        <SideChatTurns turns={[turn()]} isLoading={false} />
      </TooltipProvider>,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /How this answer was built/ }),
    );

    expect(screen.getByText(/1 pinned source/)).toBeInTheDocument();
    expect(screen.getByText(/1332 of 1800 tokens/)).toBeInTheDocument();
    expect(screen.getByText("anchored")).toBeInTheDocument();
  });

  it("explains that answers can go beyond the quoted passage before anything is asked", () => {
    render(<SideChatTurns turns={[]} isLoading={false} />);

    expect(
      screen.getByText(/grounded in the same books as the main conversation/),
    ).toBeInTheDocument();
  });

  it("says it is loading a reopened thread rather than looking empty", () => {
    render(<SideChatTurns turns={[]} isLoading />);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Loading this side chat…",
    );
  });

  it("surfaces a failed turn's error inside the window", () => {
    render(
      <SideChatTurns
        turns={[
          turn({
            status: "failed",
            error: "The study API stopped responding.",
            result: null,
          }),
        ]}
        isLoading={false}
      />,
    );

    expect(
      screen.getByText("The study API stopped responding."),
    ).toBeInTheDocument();
  });

});

describe("AnchorChips", () => {
  it("quotes every passage the side chat is anchored to", () => {
    render(
      <AnchorChips
        anchors={[
          {
            anchor_id: "a1",
            parent_turn_index: 0,
            quoted_text: "the gap compounds",
          },
          {
            anchor_id: "a2",
            parent_turn_index: 1,
            quoted_text: "features differ between training and serving",
          },
        ]}
      />,
    );

    expect(screen.getAllByRole("blockquote")).toHaveLength(2);
    expect(screen.getByText("the gap compounds")).toBeInTheDocument();
  });

  it("renders nothing when every chip has been removed", () => {
    const { container } = render(<AnchorChips anchors={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
