import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AddVideo } from "@/components/video/add-video";
import { VideoFeatureCard } from "@/components/video/video-feature-card";
import type {
  VideoDetail,
  VideoIngestion,
  VideoSummary,
  VideoTimelineEntry,
} from "@/lib/video-types";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/hooks/use-authenticated-image", () => ({
  useAuthenticatedImage: () => ({ status: "ready", url: "blob:frame" }),
}));

function job(): VideoIngestion {
  return {
    job_id: "job-1",
    video_id: "video-1",
    status: "ready",
    stage: null,
    progress: { completed: 0, total: null, unit: null, percent: null },
    attempt: 1,
    max_attempts: 3,
    retryable: false,
    cancellation_requested: false,
    actual_cost_usd: "0",
    cost_cap_usd: "1",
    error: null,
  };
}

function video(): VideoSummary {
  return {
    video_id: "video-1",
    title: "cme295-lecture1-h264.mp4",
    description: null,
    source_kind: "upload",
    duration_ms: 6_118_000,
    readiness_status: "ready",
    ready_for_qa: true,
    playback: { kind: "local", youtube_video_id: null, media_url: "/video.mp4" },
    latest_ingestion: job(),
    readiness_notes: [],
    deletable: true,
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T10:00:00Z",
    ready_at: "2026-08-05T10:00:00Z",
  };
}

function detail(): VideoDetail {
  return {
    ...video(),
    source: {
      source_kind: "upload",
      status: "ready",
      source_url: null,
      youtube_video_id: null,
      original_filename: "cme295-lecture1-h264.mp4",
    },
    chapters: [],
    resources: [
      {
        resource_id: "slides-1",
        resource_kind: "pdf",
        origin: "upload",
        status: "ready",
        title: "Slides",
        source_url: null,
        page_count: 12,
        role: "slides",
        required: false,
      },
    ],
    quality_gates: {},
  };
}

const timeline: VideoTimelineEntry[] = [
  {
    frame_id: 42,
    timestamp_ms: 30_000,
    summary: "The lecturer explains a theorem.",
    visual_types: ["slide"],
    ocr_text: "Policy Gradient Theorem",
    image_url: "/api/videos/video-1/frames/42/image",
  },
];

describe("VideoFeatureCard", () => {
  it("shows real evidence readiness and opens the lecture", () => {
    render(
      <VideoFeatureCard
        video={video()}
        detail={detail()}
        timeline={timeline}
        evidenceLoading={false}
        onDelete={vi.fn()}
        now={new Date("2026-08-15T00:00:00Z")}
      />,
    );

    expect(screen.getByRole("link", { name: /Study this lecture/ })).toHaveAttribute(
      "href",
      "/videos/video-1",
    );
    expect(screen.getByText("1 indexed frames")).toBeInTheDocument();
    expect(screen.getByText("12 pages")).toBeInTheDocument();
  });

  it("skips opening title cards when a teaching frame is available", () => {
    const frames: VideoTimelineEntry[] = [
      {
        ...timeline[0]!,
        frame_id: 1,
        summary: "A white title card displays Stanford Engineering branding.",
      },
      {
        ...timeline[0]!,
        frame_id: 2,
        summary: "The lecturer explains policy gradients at the board.",
      },
    ];

    render(
      <VideoFeatureCard
        video={video()}
        detail={detail()}
        timeline={frames}
        evidenceLoading={false}
        onDelete={vi.fn()}
      />,
    );

    expect(
      screen.getByRole("img", {
        name: "The lecturer explains policy gradients at the board.",
      }),
    ).toBeInTheDocument();
  });

  it("keeps removal behind an explicit confirmation", async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(
      <VideoFeatureCard
        video={video()}
        detail={detail()}
        timeline={timeline}
        evidenceLoading={false}
        onDelete={onDelete}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Actions for cme295-lecture1-h264.mp4" }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Remove" }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/and its transcript, frames, and conversations/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });
});

describe("AddVideo", () => {
  it("switches between branded lecture source controls", async () => {
    const user = userEvent.setup();
    render(<AddVideo onAdded={vi.fn()} />);

    expect(screen.getByLabelText("YouTube URL")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Video file" }));
    expect(screen.getByLabelText(/Choose or drop a video/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "PDF file" }));
    expect(screen.getByLabelText(/Choose or drop a PDF/)).toBeInTheDocument();
  });
});
