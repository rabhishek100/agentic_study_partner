import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VideoReferences } from "@/components/video/video-references";
import { groupBySource, partitionByCitation } from "@/lib/video-references";
import type { VideoCitationRef, VideoEvidenceRef } from "@/lib/video-types";

function evidence(
  rank: number,
  overrides: Partial<VideoEvidenceRef> = {},
): VideoEvidenceRef {
  return {
    rank,
    evidence_id: `evidence-${rank}`,
    modality: "transcript",
    excerpt: `Passage ${rank}`,
    retrieval_method: "fts",
    score: 0.5,
    start_ms: rank * 1000,
    end_ms: rank * 1000 + 500,
    page_number: null,
    frame_id: null,
    visual_event_id: null,
    transcript_segment_id: rank,
    resource_page_id: null,
    resource_id: null,
    resource_title: null,
    ...overrides,
  };
}

function citation(rank: number): VideoCitationRef {
  return {
    marker: `[S${rank}]`,
    evidence_rank: rank,
    modality: "transcript",
    start_ms: rank * 1000,
    page_number: null,
    frame_id: null,
    resource_id: null,
  };
}

const page = evidence(3, {
  modality: "resource_page",
  start_ms: null,
  end_ms: null,
  page_number: 12,
  transcript_segment_id: null,
  resource_page_id: 90,
  resource_id: "resource-a",
  resource_title: "Lecture 1 slides",
});

describe("video reference shaping", () => {
  it("keeps the recording and each linked document in separate groups", () => {
    const groups = groupBySource([
      evidence(1),
      evidence(2, { modality: "visual_frame", frame_id: 7 }),
      page,
    ]);

    expect(groups.map((group) => group.title)).toEqual([
      "This lecture",
      "Lecture 1 slides",
    ]);
    // A frame and the sentence spoken over it are the same recording.
    expect(groups[0]!.items).toHaveLength(2);
    expect(groups[1]!.resourceId).toBe("resource-a");
  });

  it("treats every reference as supporting when nothing was cited", () => {
    const { cited, uncited } = partitionByCitation([evidence(1)], []);

    expect(cited).toHaveLength(1);
    expect(uncited).toHaveLength(0);
  });

  it("demotes retrieved evidence the answer never cited", () => {
    const { cited, uncited } = partitionByCitation(
      [evidence(1), evidence(2)],
      [citation(1)],
    );

    expect(cited.map((item) => item.rank)).toEqual([1]);
    expect(uncited.map((item) => item.rank)).toEqual([2]);
  });
});

describe("VideoReferences", () => {
  it("plays a lecture passage and opens a document page", async () => {
    const onSeek = vi.fn();
    const onOpenDocument = vi.fn();
    const user = userEvent.setup();

    render(
      <VideoReferences
        evidence={[evidence(1), page]}
        citations={[citation(1), { ...citation(3), modality: "resource_page" }]}
        onSeek={onSeek}
        onOpenDocument={onOpenDocument}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Play" }));
    expect(onSeek).toHaveBeenCalledWith(1000);

    await user.click(screen.getByRole("button", { name: "Open" }));
    expect(onOpenDocument).toHaveBeenCalledWith(
      expect.objectContaining({ page_number: 12, resource_id: "resource-a" }),
    );
  });

  it("offers no way to open a page whose document was detached", () => {
    render(
      <VideoReferences
        evidence={[{ ...page, resource_id: null }]}
        citations={[]}
        onSeek={vi.fn()}
        onOpenDocument={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: "Open" })).toBeNull();
  });

  it("hides uncited retrieval behind a disclosure", async () => {
    const user = userEvent.setup();

    render(
      <VideoReferences
        evidence={[evidence(1), evidence(2)]}
        citations={[citation(1)]}
        onSeek={vi.fn()}
        onOpenDocument={vi.fn()}
      />,
    );

    expect(screen.getByText("1 source")).toBeInTheDocument();
    expect(screen.queryByText("Passage 2")).toBeNull();

    await user.click(screen.getByText("1 more retrieved, not cited"));
    expect(
      screen.getAllByRole("button", { name: "Passage" }).length,
    ).toBeGreaterThan(1);
  });
});
