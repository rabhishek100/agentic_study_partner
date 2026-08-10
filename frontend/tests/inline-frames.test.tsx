import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/use-authenticated-image", () => ({
  useAuthenticatedImage: (url: string) => ({
    status: "ready",
    url: `blob:${url}`,
  }),
}));

import { VideoAnswer, citedFrameIds } from "@/components/video/video-answer";
import { TooltipProvider } from "@/components/ui/tooltip";
import type {
  VideoCitationRef,
  VideoEvidenceRef,
} from "@/lib/video-types";

function frame(rank: number, frameId: number, at: number): VideoEvidenceRef {
  return {
    rank,
    evidence_id: `${rank}`.repeat(64).slice(0, 64),
    modality: "visual_frame",
    excerpt: `Slide ${frameId}: a scaled dot-product attention diagram`,
    retrieval_method: "hybrid",
    score: 0.8,
    start_ms: at,
    end_ms: at,
    page_number: null,
    frame_id: frameId,
    visual_event_id: null,
    transcript_segment_id: null,
    resource_page_id: null,
    resource_id: null,
    resource_title: null,
  };
}

function spoken(rank: number, at: number): VideoEvidenceRef {
  return { ...frame(rank, 0, at), modality: "transcript", frame_id: null };
}

function cite(
  marker: string,
  rank: number,
  frameId: number | null,
  at: number,
): VideoCitationRef {
  return {
    marker,
    evidence_rank: rank,
    modality: frameId ? "visual_frame" : "transcript",
    start_ms: at,
    page_number: null,
    frame_id: frameId,
    resource_id: null,
  };
}

function renderAnswer(
  answer: string,
  evidence: VideoEvidenceRef[],
  citations: VideoCitationRef[],
  onSeek = vi.fn(),
) {
  const view = render(
    <TooltipProvider>
      <VideoAnswer
        videoId="video-1"
        answer={answer}
        evidence={evidence}
        citations={citations}
        onSeek={onSeek}
        onOpenDocument={vi.fn()}
      />
    </TooltipProvider>,
  );
  return { ...view, onSeek };
}

describe("frames shown beside the claim that cites them", () => {
  it("puts each figure after the paragraph that cites it", () => {
    const { container } = renderAnswer(
      "He draws the attention diagram here. [S1]\n\n" +
        "Later he writes the softmax formula. [S2]",
      [frame(1, 41, 125_000), frame(2, 88, 300_000)],
      [cite("[S1]", 1, 41, 125_000), cite("[S2]", 2, 88, 300_000)],
    );

    // Reading order is what matters: paragraph, its figure, paragraph, its
    // figure — not two paragraphs and then a strip of pictures.
    const blocks = Array.from(container.querySelectorAll("p, figure"));
    expect(blocks.map((node) => node.tagName.toLowerCase())).toEqual([
      "p",
      "figure",
      "p",
      "figure",
    ]);
    expect(
      within(blocks[1] as HTMLElement).getByRole("img"),
    ).toHaveAttribute("src", expect.stringContaining("/frames/41/image"));
    expect(
      within(blocks[3] as HTMLElement).getByRole("img"),
    ).toHaveAttribute("src", expect.stringContaining("/frames/88/image"));
  });

  it("shows a frame once however often it is cited", () => {
    const { container } = renderAnswer(
      "First mention. [S1]\n\nSecond mention of the same slide. [S1]",
      [frame(1, 41, 125_000)],
      [cite("[S1]", 1, 41, 125_000)],
    );
    expect(container.querySelectorAll("figure")).toHaveLength(1);
  });

  it("shows nothing for a claim cited only to the transcript", () => {
    const { container } = renderAnswer(
      "He says attention gives a direct link. [S1]",
      [spoken(1, 400_000)],
      [cite("[S1]", 1, null, 400_000)],
    );
    expect(container.querySelectorAll("figure")).toHaveLength(0);
  });

  it("seeks the lecture when a figure is clicked", async () => {
    const user = userEvent.setup();
    const { onSeek } = renderAnswer(
      "The diagram appears here. [S1]",
      [frame(1, 41, 125_000)],
      [cite("[S1]", 1, 41, 125_000)],
    );

    await user.click(screen.getByLabelText(/Play from 2:05/));
    expect(onSeek).toHaveBeenCalledWith(125_000);
  });

  it("captions the figure with the frame's own description", () => {
    renderAnswer(
      "The diagram appears here. [S1]",
      [frame(1, 41, 125_000)],
      [cite("[S1]", 1, 41, 125_000)],
    );
    // The description the model was shown, not prose the interface invented
    // about a picture it cannot see.
    expect(
      screen.getByAltText(/scaled dot-product attention diagram/),
    ).toBeInTheDocument();
  });

  it("keeps a cited frame mounted through an unrelated rerender", () => {
    const refs = [frame(1, 41, 125_000)];
    const citations = [cite("[S1]", 1, 41, 125_000)];
    const first = renderAnswer(
      "The diagram appears here. [S1]",
      refs,
      citations,
    );
    const original = screen.getByAltText(
      /scaled dot-product attention diagram/,
    );

    first.rerender(
      <TooltipProvider>
        <VideoAnswer
          videoId="video-1"
          answer="The diagram appears here. [S1]"
          evidence={refs}
          citations={citations}
          onSeek={first.onSeek}
          onOpenDocument={vi.fn()}
        />
      </TooltipProvider>,
    );

    expect(
      screen.getByAltText(/scaled dot-product attention diagram/),
    ).toBe(original);
  });
});

describe("citedFrameIds", () => {
  it("names the frames the answer showed, so they are not repeated below", () => {
    const ids = citedFrameIds([
      cite("[S1]", 1, 41, 125_000),
      cite("[S2]", 2, null, 300_000),
      cite("[S3]", 3, 88, 400_000),
    ]);
    expect(ids).toEqual(new Set([41, 88]));
  });

  it("is empty when nothing visual was cited", () => {
    expect(citedFrameIds([cite("[S1]", 1, null, 10)])).toEqual(new Set());
  });
});
