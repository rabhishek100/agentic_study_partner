import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ProcessingBand } from "@/components/video/processing-band";
import { monogram } from "@/components/video/video-poster";
import { VideoTile } from "@/components/video/video-tile";
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
    title: "cme295-lecture1-h264.mp4",
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

const NOW = new Date("2026-08-19T09:00:00Z");

describe("monogram", () => {
  it("reads a filename's separators as word breaks and drops the extension", () => {
    expect(monogram("cme295-lecture1-h264.mp4")).toBe("CL");
  });

  it("uses the first two words of a real title", () => {
    expect(monogram("Convex Optimization — Duality")).toBe("CO");
  });

  it("never renders empty", () => {
    expect(monogram("—")).toBe("—");
  });
});

describe("video tile", () => {
  it("carries the poster, the duration, and no ready badge", () => {
    render(<ul><VideoTile video={video()} onDelete={vi.fn()} now={NOW} /></ul>);

    expect(screen.getByText("1:41:58")).toBeInTheDocument();
    expect(screen.getByRole("link")).toHaveAttribute("href", "/videos/video-1");
    expect(screen.queryByText(/^ready$/i)).not.toBeInTheDocument();
  });

  it("uses YouTube's own thumbnail when the lecture is a YouTube one", () => {
    render(
      <ul>
        <VideoTile
          video={video({
            source_kind: "youtube",
            playback: {
              kind: "youtube",
              youtube_video_id: "abc123",
              media_url: null,
            },
          })}
          onDelete={vi.fn()}
          now={NOW}
        />
      </ul>,
    );

    const poster = document.querySelector("img");
    expect(poster).toHaveAttribute(
      "src",
      "https://i.ytimg.com/vi/abc123/mqdefault.jpg",
    );
  });

  it("falls back to a monogram when there is no image to show", () => {
    render(<ul><VideoTile video={video()} onDelete={vi.fn()} now={NOW} /></ul>);

    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("CL")).toBeInTheDocument();
  });

  it("says what a partial lecture is missing, measured", () => {
    render(
      <ul>
        <VideoTile
          video={video({
            readiness_status: "degraded",
            readiness_notes: ["Visual coverage reached 61% of the timeline."],
          })}
          onDelete={vi.fn()}
          now={NOW}
        />
      </ul>,
    );

    expect(
      screen.getByText("Visual coverage reached 61% of the timeline."),
    ).toBeInTheDocument();
  });

  it("confirms before removing, and names what goes with it", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(<ul><VideoTile video={video()} onDelete={onDelete} now={NOW} /></ul>);

    await user.click(
      screen.getByRole("button", { name: "Actions for cme295-lecture1-h264.mp4" }),
    );
    await user.click(await screen.findByRole("menuitem", { name: /remove/i }));

    expect(
      screen.getByText(/transcript, frames, and\s+conversations go with it/),
    ).toBeInTheDocument();
    expect(onDelete).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(onDelete).toHaveBeenCalledTimes(1);
  });
});

describe("processing band", () => {
  it("renders nothing when nothing is processing", () => {
    const { container } = render(<ProcessingBand videos={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("counts the runs and says they survive leaving the page", () => {
    render(
      <ProcessingBand
        videos={[
          video({ video_id: "a", readiness_status: "processing" }),
          video({ video_id: "b", title: "Second", readiness_status: "processing" }),
        ]}
      />,
    );

    expect(screen.getByText("Processing 2 lectures")).toBeInTheDocument();
    expect(
      screen.getByText(/keeps running if you close the page/i),
    ).toBeInTheDocument();
  });

  it("names the stage a run has reached and how far through the pipeline it is", () => {
    render(
      <ProcessingBand
        videos={[
          video({
            readiness_status: "processing",
            latest_ingestion: job({
              status: "running",
              stage: "visual_analysis",
              progress: { completed: 40, total: 80, unit: "frames", percent: 58 },
            }),
          }),
        ]}
      />,
    );

    const band = screen.getByRole("status");
    expect(within(band).getByText("Interpreting what is shown")).toBeInTheDocument();
    expect(within(band).getByText("step 7 of 12")).toBeInTheDocument();
    expect(within(band).getByRole("progressbar")).toHaveAttribute(
      "aria-valuenow",
      "58",
    );
  });
});
