import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VideoAnswer } from "@/components/video/video-answer";
import { TooltipProvider } from "@/components/ui/tooltip";
import { formatTimestamp } from "@/lib/video-types";
import type {
  VideoCitationRef,
  VideoDocumentTarget,
  VideoEvidenceRef,
} from "@/lib/video-types";

function evidence(overrides: Partial<VideoEvidenceRef> = {}): VideoEvidenceRef {
  return {
    rank: 1,
    evidence_id: "a".repeat(64),
    modality: "visual_frame",
    excerpt: "A scaled dot-product attention diagram is drawn",
    retrieval_method: "hybrid",
    score: 0.81,
    start_ms: 125_000,
    end_ms: 125_000,
    page_number: null,
    frame_id: 4,
    visual_event_id: null,
    transcript_segment_id: null,
    resource_page_id: null,
    resource_id: null,
    resource_title: null,
    ...overrides,
  };
}

function citation(
  overrides: Partial<VideoCitationRef> = {},
): VideoCitationRef {
  return {
    marker: "[S1]",
    evidence_rank: 1,
    modality: "visual_frame",
    start_ms: 125_000,
    page_number: null,
    frame_id: 4,
    resource_id: null,
    ...overrides,
  };
}

function renderAnswer(
  answer: string,
  refs: VideoEvidenceRef[],
  citations: VideoCitationRef[],
  handlers: {
    onSeek?: (ms: number) => void;
    onOpenDocument?: (target: VideoDocumentTarget) => void;
  } = {},
) {
  return render(
    <TooltipProvider>
      <VideoAnswer
        answer={answer}
        evidence={refs}
        citations={citations}
        onSeek={handlers.onSeek ?? (() => {})}
        onOpenDocument={handlers.onOpenDocument ?? (() => {})}
      />
    </TooltipProvider>,
  );
}

describe("VideoAnswer", () => {
  it("turns a timestamp marker into a control that seeks the player", async () => {
    const onSeek = vi.fn();
    renderAnswer(
      "He draws the attention diagram [S1] on the board.",
      [evidence()],
      [citation()],
      { onSeek },
    );

    const control = screen.getByRole("button", {
      name: formatTimestamp(125_000),
    });
    await userEvent.click(control);
    expect(onSeek).toHaveBeenCalledWith(125_000);
  });

  it("labels a page citation by its document rather than a timestamp", async () => {
    const onOpenDocument = vi.fn();
    const page = evidence({
      rank: 2,
      modality: "resource_page",
      start_ms: null,
      end_ms: null,
      page_number: 12,
      frame_id: null,
      resource_page_id: 3,
      resource_id: "r-1",
      resource_title: "CME295 slides",
      excerpt: "Positional encoding",
    });
    renderAnswer(
      "The slides define it [S2].",
      [page],
      [
        citation({
          marker: "[S2]",
          evidence_rank: 2,
          modality: "resource_page",
          start_ms: null,
          page_number: 12,
          frame_id: null,
          resource_id: "r-1",
        }),
      ],
      { onOpenDocument },
    );

    const control = screen.getByRole("button", {
      name: "CME295 slides p. 12",
    });
    await userEvent.click(control);
    // The passage to highlight comes from the evidence, not the marker.
    expect(onOpenDocument).toHaveBeenCalledWith({
      resourceId: "r-1",
      page: 12,
      excerpt: "Positional encoding",
    });
  });

  it("leaves a marker as plain text when nothing backs it", () => {
    renderAnswer("An unsupported claim [S9].", [evidence()], [citation()]);

    expect(screen.queryByRole("button", { name: /S9/ })).toBeNull();
    expect(screen.getByText(/\[S9\]/)).toBeTruthy();
  });
});

describe("formatTimestamp", () => {
  it("uses hours only when the lecture is long enough to need them", () => {
    expect(formatTimestamp(0)).toBe("0:00");
    expect(formatTimestamp(125_000)).toBe("2:05");
    expect(formatTimestamp(3_725_000)).toBe("1:02:05");
    expect(formatTimestamp(null)).toBe("--:--");
  });
});
