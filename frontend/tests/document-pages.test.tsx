import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  DocumentPages,
  citedPages,
  pageImageSource,
} from "@/components/video/document-pages";
import type { VideoCitationRef, VideoEvidenceRef } from "@/lib/video-types";

vi.mock("@/hooks/use-authenticated-image", () => ({
  useAuthenticatedImage: (url: string) => ({
    status: "ready" as const,
    url: `blob:${url}`,
  }),
}));

function pageEvidence(
  rank: number,
  overrides: Partial<VideoEvidenceRef> = {},
): VideoEvidenceRef {
  return {
    rank,
    evidence_id: `evidence-${rank}`,
    modality: "resource_page",
    excerpt: `Slide text ${rank}`,
    retrieval_method: "fts",
    score: 0.4,
    start_ms: null,
    end_ms: null,
    page_number: 5,
    frame_id: null,
    visual_event_id: null,
    transcript_segment_id: null,
    resource_page_id: rank,
    resource_id: "resource-a",
    resource_title: "Lecture slides",
    ...overrides,
  };
}

function citation(rank: number): VideoCitationRef {
  return {
    marker: `[S${rank}]`,
    evidence_rank: rank,
    modality: "resource_page",
    start_ms: null,
    page_number: 5,
    frame_id: null,
    resource_id: "resource-a",
  };
}

describe("citedPages", () => {
  it("shows a page once however many markers cite it", () => {
    const pages = citedPages(
      [pageEvidence(1), pageEvidence(2)],
      [citation(1), citation(2)],
    );

    expect(pages).toHaveLength(1);
  });

  it("leaves out pages retrieval surfaced but the answer never cited", () => {
    const pages = citedPages(
      [pageEvidence(1), pageEvidence(2, { page_number: 9 })],
      [citation(1)],
    );

    expect(pages.map((item) => item.page_number)).toEqual([5]);
  });

  it("skips transcript and frame evidence entirely", () => {
    const pages = citedPages(
      [
        pageEvidence(1),
        pageEvidence(2, { modality: "transcript", page_number: null }),
      ],
      [citation(1), citation(2)],
    );

    expect(pages).toHaveLength(1);
  });

  it("caps how many pages one answer can put on screen", () => {
    const many = [1, 2, 3, 4, 5, 6].map((rank) =>
      pageEvidence(rank, { page_number: rank }),
    );

    expect(citedPages(many, many.map((item) => citation(item.rank)))).toHaveLength(
      4,
    );
  });
});

describe("DocumentPages", () => {
  it("renders the cited page and opens the viewer there", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();

    render(
      <DocumentPages
        videoId="video-1"
        evidence={[pageEvidence(1)]}
        citations={[citation(1)]}
        onOpen={onOpen}
      />,
    );

    const image = screen.getByAltText("Lecture slides, page 5");
    expect(image).toHaveAttribute(
      "src",
      `blob:${pageImageSource("video-1", "resource-a", 5)}`,
    );

    await user.click(image);
    expect(onOpen).toHaveBeenCalledWith({
      resourceId: "resource-a",
      page: 5,
      excerpt: "Slide text 1",
    });
  });

  it("renders nothing when the answer cited no page", () => {
    const { container } = render(
      <DocumentPages
        videoId="video-1"
        evidence={[pageEvidence(1)]}
        citations={[]}
        onOpen={vi.fn()}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
