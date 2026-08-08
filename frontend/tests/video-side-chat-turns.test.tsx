import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoSideChatTurns } from "@/components/video/video-side-chat-turns";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { SideChatTurn } from "@/lib/side-chat";
import type { VideoTurnResult } from "@/lib/video-types";

function result(overrides: Partial<VideoTurnResult> = {}): VideoTurnResult {
  return {
    question: "Why does that matter?",
    answer: "Because the frame shows it [S1].",
    route: "evidence_qa",
    history_dependency: "dependent",
    standalone_query: "why does the attention diagram matter?",
    evidence: [
      {
        rank: 1,
        evidence_id: "a".repeat(64),
        modality: "visual_frame",
        excerpt: "the attention diagram",
        retrieval_method: "anchor_pin",
        score: 1,
        start_ms: 42_000,
        end_ms: 45_000,
        page_number: null,
        frame_id: 7,
        visual_event_id: null,
        transcript_segment_id: null,
        resource_page_id: null,
        resource_id: null,
        resource_title: null,
      },
    ],
    citations: [
      {
        marker: "[S1]",
        evidence_rank: 1,
        modality: "visual_frame",
        start_ms: 42_000,
        page_number: null,
        frame_id: 7,
        resource_id: null,
      },
    ],
    visual_cards: [],
    outcome: "answer",
    ingestion_version_id: "version-1",
    retrieval_attempts: 1,
    sufficiency_reason: "1 evidence item",
    routing_reason: "question about a highlighted passage",
    cost_usd: 0.002,
    trace_id: null,
    warnings: [],
    side_context: {
      anchor_ids: ["a1"],
      pinned_chunk_ids: ["a".repeat(64)],
      token_count: 640,
      token_budget: 1800,
      dropped: [],
    },
    ...overrides,
  };
}

function turn(
  overrides: Partial<SideChatTurn<VideoTurnResult>> = {},
): SideChatTurn<VideoTurnResult> {
  return {
    id: "turn-1",
    question: "Why does that matter?",
    answer: "Because the frame shows it [S1].",
    status: "complete",
    result: result(),
    error: null,
    ...overrides,
  };
}

function renderTurns(
  turns: SideChatTurn<VideoTurnResult>[],
  extra: { isLoading?: boolean; isQueued?: boolean } = {},
) {
  const onSeek = vi.fn();
  const onOpenDocument = vi.fn();
  render(
    <TooltipProvider>
      <VideoSideChatTurns
        videoId="video-1"
        turns={turns}
        isLoading={extra.isLoading ?? false}
        isQueued={extra.isQueued ?? false}
        onSeek={onSeek}
        onOpenDocument={onOpenDocument}
      />
    </TooltipProvider>,
  );
  return { onSeek, onOpenDocument };
}

describe("VideoSideChatTurns", () => {
  it("seeks the player from a citation, like the main lecture chat", () => {
    const { onSeek } = renderTurns([turn()]);

    // The marker renders as a timestamp chip; clicking it is what seeks.
    fireEvent.click(screen.getAllByRole("button", { name: /0:42/ })[0]!);

    expect(onSeek).toHaveBeenCalledWith(42_000);
  });

  it("shows the anchored context report for a side turn", () => {
    renderTurns([turn()]);

    fireEvent.click(
      screen.getByRole("button", { name: /How this answer was built/ }),
    );

    expect(screen.getByText(/1 pinned source/)).toBeInTheDocument();
    expect(screen.getByText(/640 of 1800 tokens/)).toBeInTheDocument();
  });

  it("says a queued turn is waiting rather than searching", () => {
    renderTurns(
      [turn({ status: "streaming", answer: "", result: null })],
      { isQueued: true },
    );

    expect(screen.getByRole("status")).toHaveTextContent(
      "Waiting for the other side chats to finish…",
    );
  });

  it("says it is searching the lecture once it holds a slot", () => {
    renderTurns([turn({ status: "streaming", answer: "", result: null })]);

    expect(screen.getByRole("status")).toHaveTextContent(
      "Searching the lecture…",
    );
  });

  it("explains that answers can go beyond the quoted passage", () => {
    renderTurns([]);

    expect(
      screen.getByText(/search the same lecture as the main conversation/),
    ).toBeInTheDocument();
  });

  it("surfaces a failed turn's error inside the window", () => {
    renderTurns([
      turn({
        status: "failed",
        error: "The lecture API stopped responding.",
        result: null,
      }),
    ]);

    expect(
      screen.getByText("The lecture API stopped responding."),
    ).toBeInTheDocument();
  });

  it("renders plain prose mid-stream, before markers can resolve", () => {
    renderTurns([
      turn({ status: "streaming", answer: "Because the frame", result: null }),
    ]);

    expect(screen.getByText("Because the frame")).toBeInTheDocument();
  });
});
