import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { VideoCard } from "@/components/video/video-card";
import type { VideoIngestion, VideoSummary } from "@/lib/video-types";

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

function job(overrides: Partial<VideoIngestion> = {}): VideoIngestion {
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
    ...overrides,
  };
}

function video(overrides: Partial<VideoSummary> = {}): VideoSummary {
  return {
    video_id: "video-1",
    title: "cme295-lecture1.mp4",
    description: null,
    source_kind: "upload",
    duration_ms: 6_118_760,
    readiness_status: "ready",
    ready_for_qa: true,
    playback: { kind: "local", youtube_video_id: null, media_url: "/x.mp4" },
    latest_ingestion: job(),
    readiness_notes: [],
    deletable: true,
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T10:00:00Z",
    ready_at: "2026-08-05T10:00:00Z",
    ...overrides,
  };
}

function card(overrides: Partial<VideoSummary> = {}, handlers = {}) {
  return (
    <ul>
      <VideoCard
        video={video(overrides)}
        onRetry={vi.fn()}
        onDelete={vi.fn()}
        now={new Date("2026-08-06T00:00:00Z")}
        {...handlers}
      />
    </ul>
  );
}

describe("VideoCard", () => {
  it("opens a usable lecture and carries no status chip", () => {
    render(card());

    expect(screen.getByRole("link")).toHaveAttribute("href", "/videos/video-1");
    // Under a heading that already says "Ready to ask", a chip on every row
    // is noise that hides the one row saying something different.
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.getByText(/Uploaded · 1:41:58 · added/)).toBeInTheDocument();
  });

  it("states what a partial lecture is missing, measured", () => {
    render(
      card({
        readiness_status: "degraded",
        readiness_notes: ["The transcript covers 94% of the lecture."],
      }),
    );

    expect(
      screen.getByText("The transcript covers 94% of the lecture."),
    ).toBeInTheDocument();
    // Still a link: a partial lecture answers questions.
    expect(screen.getByRole("link")).toBeInTheDocument();
  });

  it("does not link a failed lecture to a workspace with nothing to answer", () => {
    render(
      card({
        readiness_status: "failed",
        ready_for_qa: false,
        latest_ingestion: job({
          status: "failed",
          error: {
            code: "invalid_media",
            message: "The source is not a playable video.",
          },
        }),
      }),
    );

    expect(screen.queryByRole("link")).toBeNull();
    // The worker's own sentence, not one constant for every failure.
    expect(
      screen.getByText("The source is not a playable video."),
    ).toBeInTheDocument();
  });

  it("offers a retry only when the failure can be retried", () => {
    const failed = {
      readiness_status: "failed" as const,
      ready_for_qa: false,
      latest_ingestion: job({
        status: "failed" as const,
        error: { code: "invalid_media", message: "Not playable." },
      }),
    };

    const { rerender } = render(card(failed));
    expect(screen.queryByRole("button", { name: /Try again/ })).toBeNull();

    rerender(
      card({
        ...failed,
        latest_ingestion: job({
          status: "failed",
          retryable: true,
          error: { code: "provider_unavailable", message: "Provider down." },
        }),
      }),
    );
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });

  it("says why a lecture cannot be removed instead of failing on the attempt", () => {
    render(
      card({
        deletable: false,
        readiness_status: "failed",
        ready_for_qa: false,
        latest_ingestion: job({
          status: "failed",
          error: { code: "invalid_media", message: "Not playable." },
        }),
      }),
    );

    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    // Explains the constraint rather than offering a button that would 409.
    expect(
      screen.getByText(/removing it would leave the file behind/),
    ).toBeInTheDocument();
  });

  it("confirms before removing, and names what goes with it", async () => {
    const onDelete = vi.fn();
    const user = userEvent.setup();
    render(
      card(
        {
          readiness_status: "failed",
          ready_for_qa: false,
          latest_ingestion: job({
            status: "failed",
            error: { code: "invalid_media", message: "Not playable." },
          }),
        },
        { onDelete },
      ),
    );

    await user.click(screen.getByRole("button", { name: /Remove/ }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByText(/cannot be undone/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(onDelete).toHaveBeenCalledOnce();
  });

  it("shows a processing lecture where it has got to", () => {
    render(
      card({
        readiness_status: "processing",
        ready_for_qa: false,
        latest_ingestion: job({ status: "running", stage: "visual_analysis" }),
      }),
    );

    expect(screen.getByText("Interpreting what is shown")).toBeInTheDocument();
    expect(screen.getByText(/step 7 of 12/)).toBeInTheDocument();
  });

  it("keeps a stalled upload reachable so the file can be sent again", () => {
    render(
      card({
        readiness_status: "processing",
        ready_for_qa: false,
        latest_ingestion: job({ status: "awaiting_upload" }),
      }),
    );

    expect(screen.getByRole("link")).toBeInTheDocument();
    expect(screen.getByText(/upload never finished/)).toBeInTheDocument();
  });
});
