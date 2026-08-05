import { describe, expect, it } from "vitest";

import { formatAdded, groupVideos, videoState } from "@/lib/video-state";
import type { VideoIngestion, VideoSummary } from "@/lib/video-types";

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
    deletable: false,
    created_at: "2026-08-05T10:00:00Z",
    updated_at: "2026-08-05T10:00:00Z",
    ready_at: "2026-08-05T10:00:00Z",
    ...overrides,
  };
}

describe("videoState", () => {
  it("reads a published version as ready", () => {
    expect(videoState(video())).toBe("ready");
  });

  it("calls a degraded version partial only when it can say why", () => {
    const explained = video({
      readiness_status: "degraded",
      readiness_notes: ["The transcript covers 94% of the lecture."],
    });
    expect(videoState(explained)).toBe("partial");

    // An unexplained reservation is a worry with no content, so a version
    // that cannot say what it missed is simply ready.
    expect(videoState(video({ readiness_status: "degraded" }))).toBe("ready");
  });

  it("separates a stalled upload from work in progress", () => {
    expect(
      videoState(
        video({
          readiness_status: "processing",
          ready_for_qa: false,
          latest_ingestion: job({ status: "awaiting_upload" }),
        }),
      ),
    ).toBe("awaiting_upload");

    expect(
      videoState(
        video({
          readiness_status: "processing",
          ready_for_qa: false,
          latest_ingestion: job({ status: "running", stage: "ocr" }),
        }),
      ),
    ).toBe("processing");
  });

  it("reads a failed ingestion as failed", () => {
    expect(
      videoState(
        video({
          readiness_status: "failed",
          ready_for_qa: false,
          latest_ingestion: job({
            status: "failed",
            error: { code: "invalid_media", message: "Not a playable video." },
          }),
        }),
      ),
    ).toBe("failed");
  });

  it("keeps a republished video ready after an earlier cancellation", () => {
    // A cancelled job beside a video that already answers questions is
    // history, not a state the reader has to act on.
    expect(
      videoState(video({ latest_ingestion: job({ status: "cancelled" }) })),
    ).toBe("ready");
  });
});

describe("groupVideos", () => {
  it("puts the usable lectures first and the broken ones last", () => {
    const groups = groupVideos([
      video({
        video_id: "failed",
        readiness_status: "failed",
        ready_for_qa: false,
        latest_ingestion: job({ status: "failed", error: { code: "x", message: "y" } }),
      }),
      video({
        video_id: "processing",
        readiness_status: "processing",
        ready_for_qa: false,
        latest_ingestion: job({ status: "running", stage: "ocr" }),
      }),
      video({ video_id: "ready" }),
    ]);

    expect(groups.map((group) => group.key)).toEqual([
      "library",
      "processing",
      "attention",
    ]);
    expect(groups[0]!.videos.map((item) => item.video_id)).toEqual(["ready"]);
  });

  it("shows no heading for a group with nothing in it", () => {
    const groups = groupVideos([video()]);

    // A "Needs attention" heading over nothing is a false alarm on every load.
    expect(groups.map((group) => group.key)).toEqual(["library"]);
  });

  it("keeps a stalled upload with the things needing a decision", () => {
    const groups = groupVideos([
      video({
        readiness_status: "processing",
        ready_for_qa: false,
        latest_ingestion: job({ status: "awaiting_upload" }),
      }),
    ]);

    expect(groups.map((group) => group.key)).toEqual(["attention"]);
  });
});

describe("formatAdded", () => {
  it("distinguishes same-named uploads without spelling out the year", () => {
    const now = new Date("2026-08-06T00:00:00Z");
    expect(formatAdded("2026-08-05T10:00:00Z", now)).toMatch(/Aug/);
    expect(formatAdded("2026-08-05T10:00:00Z", now)).not.toMatch(/2026/);
    expect(formatAdded("2025-01-02T10:00:00Z", now)).toMatch(/2025/);
  });

  it("times today's uploads, which a date alone cannot separate", () => {
    // The real case: three attempts at one file, minutes apart, all "Aug 5".
    const now = new Date("2026-08-06T12:00:00");
    const first = formatAdded(new Date("2026-08-06T09:04:00").toISOString(), now);
    const second = formatAdded(new Date("2026-08-06T09:31:00").toISOString(), now);

    expect(first).toMatch(/^today, /);
    expect(first).not.toEqual(second);
  });

  it("says nothing rather than NaN for an unusable date", () => {
    expect(formatAdded("not a date")).toBe("");
  });
});
