import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VideoTurnView } from "@/components/video/video-turn";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { VideoTurn, VideoTurnResult } from "@/lib/video-types";

const result: VideoTurnResult = {
  question: "What was drawn?",
  answer: "A bias-variance curve [S1].",
  route: "evidence_qa",
  history_dependency: "independent",
  standalone_query: "What was drawn on the board?",
  evidence: [
    {
      rank: 1,
      evidence_id: "evidence-1",
      modality: "visual_frame",
      excerpt: "A curve labelled error against complexity",
      retrieval_method: "image_vector",
      score: 0.71,
      start_ms: 61_000,
      end_ms: null,
      page_number: null,
      frame_id: 12,
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
      start_ms: 61_000,
      page_number: null,
      frame_id: 12,
      resource_id: null,
    },
  ],
  visual_cards: [],
  outcome: "answer",
  ingestion_version_id: "version-1",
  retrieval_attempts: 1,
  sufficiency_reason: "1 evidence item across the lecture's modalities.",
  routing_reason: "First turn in the conversation.",
  cost_usd: 0.0123,
  trace_id: "abcdef1234567890",
  warnings: [],
};

function view(turn: Partial<VideoTurn>, props: Record<string, unknown> = {}) {
  return (
    <TooltipProvider>
      <VideoTurnView
      videoId="video-1"
      turn={{
        id: "turn-1",
        question: "What was drawn?",
        answer: result.answer,
        status: "complete",
        result,
        error: null,
        ...turn,
      }}
      isLast
      canRetry
      onRetry={vi.fn()}
      onSeek={vi.fn()}
        onOpenDocument={vi.fn()}
        {...props}
      />
    </TooltipProvider>
  );
}

describe("VideoTurnView", () => {
  it("shows raw prose while streaming, since markers cannot resolve yet", () => {
    render(
      view({
        status: "streaming",
        answer: "A bias-variance curve [S1].",
        result: null,
      }),
    );

    // The literal marker is still present as text — what must not happen is a
    // control rendered for a citation the turn has not reported yet.
    expect(
      screen.getByText("A bias-variance curve [S1]."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /1:01/ })).toBeNull();
  });

  it("announces work before any tokens arrive", () => {
    render(view({ status: "streaming", answer: "", result: null }));

    expect(screen.getByRole("status")).toHaveTextContent(
      "Searching the lecture…",
    );
  });

  it("separates the reading view from the diagnostic view", async () => {
    const user = userEvent.setup();
    render(view({}));

    // Sources are readable without opening anything.
    expect(screen.getByText("1 source")).toBeInTheDocument();
    // Scores live behind the inspector.
    expect(screen.queryByText("0.710")).toBeNull();

    await user.click(
      screen.getByRole("button", { name: /How this answer was built/ }),
    );
    expect(screen.getByText("0.710")).toBeInTheDocument();
    expect(screen.getByText("What was drawn on the board?")).toBeInTheDocument();
  });

  it("offers Try again on a failed turn and says a stopped turn was dropped", () => {
    const { rerender } = render(
      view({ status: "failed", answer: "", result: null, error: "Timed out" }),
    );
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
    expect(screen.getByText("Timed out")).toBeInTheDocument();

    rerender(view({ status: "stopped" }));
    expect(
      screen.getByText(/was not added to the conversation/),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Regenerate/ })).toBeInTheDocument();
  });
});
