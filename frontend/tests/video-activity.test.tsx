import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContinueBand } from "@/components/video/continue-band";
import { VideoTile } from "@/components/video/video-tile";
import {
  formatSince,
  latestByVideo,
  matchesQuery,
  sortByActivity,
} from "@/lib/video-activity";
import type {
  VideoConversationSummary,
  VideoIngestion,
  VideoSummary,
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

const NOW = new Date("2026-08-19T12:00:00Z");

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

function video(id: string, overrides: Partial<VideoSummary> = {}): VideoSummary {
  return {
    video_id: id,
    title: `Lecture ${id}`,
    description: null,
    source_kind: "upload",
    duration_ms: 3_600_000,
    readiness_status: "ready",
    ready_for_qa: true,
    playback: { kind: "local", youtube_video_id: null, media_url: "/x.mp4" },
    latest_ingestion: job(),
    readiness_notes: [],
    poster_frame_id: null,
    chapter_count: 0,
    slide_count: 0,
    deletable: true,
    created_at: "2026-08-01T10:00:00Z",
    updated_at: "2026-08-01T10:00:00Z",
    ready_at: "2026-08-01T10:00:00Z",
    ...overrides,
  };
}

function conversation(
  overrides: Partial<VideoConversationSummary> = {},
): VideoConversationSummary {
  return {
    conversation_id: "conv-1",
    video_id: "1",
    video_title: "Lecture 1",
    title: "Why does the attention mask matter?",
    turn_count: 4,
    created_at: "2026-08-19T09:00:00Z",
    updated_at: "2026-08-19T10:00:00Z",
    ...overrides,
  };
}

describe("latestByVideo", () => {
  it("keeps the newest conversation per lecture regardless of arrival order", () => {
    const latest = latestByVideo([
      conversation({ conversation_id: "old", updated_at: "2026-08-10T10:00:00Z" }),
      conversation({ conversation_id: "new", updated_at: "2026-08-19T10:00:00Z" }),
    ]);

    expect(latest.get("1")?.conversation_id).toBe("new");
  });
});

describe("formatSince", () => {
  it("counts in the unit the reader is still thinking in", () => {
    expect(formatSince("2026-08-19T11:59:30Z", NOW)).toBe("just now");
    expect(formatSince("2026-08-19T11:20:00Z", NOW)).toBe("40m ago");
    expect(formatSince("2026-08-19T09:00:00Z", NOW)).toBe("3h ago");
    expect(formatSince("2026-08-18T09:00:00Z", NOW)).toBe("yesterday");
    expect(formatSince("2026-08-16T09:00:00Z", NOW)).toBe("3 days ago");
  });

  it("falls back to a date once the count stops placing it", () => {
    expect(formatSince("2026-07-04T09:00:00Z", NOW)).toMatch(/Jul/);
  });
});

describe("sortByActivity", () => {
  it("puts what was asked most recently first, then what arrived most recently", () => {
    const videos = [
      video("cold-old", { created_at: "2026-08-01T10:00:00Z" }),
      video("asked-older"),
      video("cold-new", { created_at: "2026-08-18T10:00:00Z" }),
      video("asked-latest"),
    ];
    const latest = latestByVideo([
      conversation({ video_id: "asked-older", updated_at: "2026-08-12T10:00:00Z" }),
      conversation({ video_id: "asked-latest", updated_at: "2026-08-19T10:00:00Z" }),
    ]);

    expect(sortByActivity(videos, latest).map((v) => v.video_id)).toEqual([
      "asked-latest",
      "asked-older",
      "cold-new",
      "cold-old",
    ]);
  });
});

describe("matchesQuery", () => {
  it("finds a filename by the words inside it", () => {
    const upload = video("1", { title: "cme295-lecture1-h264.mp4" });
    expect(matchesQuery(upload, "cme295 lecture")).toBe(true);
    expect(matchesQuery(upload, "CME295-Lecture")).toBe(true);
    expect(matchesQuery(upload, "optimization")).toBe(false);
  });

  it("reaches across the space a prose title puts inside a course code", () => {
    const lecture = video("2", {
      title: "CME 295 — Transformers & LLMs, Lecture 1",
    });
    expect(matchesQuery(lecture, "cme295 lecture")).toBe(true);
  });

  it("matches tokens in any position, not as one phrase", () => {
    const lecture = video("3", { title: "Statistical Learning — Bias and Variance" });
    expect(matchesQuery(lecture, "learning bias")).toBe(true);
    expect(matchesQuery(lecture, "learning diffusion")).toBe(false);
  });

  it("matches everything on an empty query", () => {
    expect(matchesQuery(video("1"), "   ")).toBe(true);
  });
});

describe("continue band", () => {
  it("deep-links to the conversation, not just the lecture", () => {
    render(<ContinueBand conversations={[conversation()]} now={NOW} />);

    expect(
      screen.getByRole("link", { name: /Why does the attention mask matter/ }),
    ).toHaveAttribute("href", "/videos/1?conversation=conv-1");
    expect(screen.getByText(/Lecture 1 · 2h ago/)).toBeInTheDocument();
  });

  it("stays out of the way when there is nothing to continue", () => {
    const { container } = render(<ContinueBand conversations={[]} now={NOW} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("is a shortcut rather than a history: it shows at most three", () => {
    render(
      <ContinueBand
        conversations={[1, 2, 3, 4, 5].map((n) =>
          conversation({ conversation_id: `c-${n}`, video_id: `v-${n}` }),
        )}
        now={NOW}
      />,
    );

    expect(screen.getAllByRole("link")).toHaveLength(3);
  });
});

describe("video tile activity", () => {
  it("says when a lecture was last asked instead of when it arrived", () => {
    render(
      <ul>
        <VideoTile
          video={video("1")}
          onDelete={vi.fn()}
          lastAsked="2026-08-19T10:00:00Z"
          now={NOW}
        />
      </ul>,
    );

    expect(screen.getByText("Uploaded · asked 2h ago")).toBeInTheDocument();
  });

  it("falls back to the added date for a lecture never asked anything", () => {
    render(<ul><VideoTile video={video("1")} onDelete={vi.fn()} now={NOW} /></ul>);

    expect(screen.getByText(/Uploaded · added/)).toBeInTheDocument();
  });
});
