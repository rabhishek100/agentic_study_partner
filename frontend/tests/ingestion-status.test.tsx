import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  IngestionStatus,
  readinessLabel,
} from "@/components/video/ingestion-status";
import type { VideoIngestion } from "@/lib/video-types";

function ingestion(overrides: Partial<VideoIngestion> = {}): VideoIngestion {
  return {
    job_id: "job-1",
    video_id: "video-1",
    status: "running",
    stage: "acquire_source",
    progress: { completed: 0, total: null, unit: null, percent: null },
    attempt: 1,
    max_attempts: 3,
    retryable: false,
    cancellation_requested: false,
    actual_cost_usd: "0.000000",
    cost_cap_usd: "0.500000",
    error: null,
    ...overrides,
  };
}

describe("IngestionStatus", () => {
  it("says a reserved upload is waiting, not processing", () => {
    // The reservation sits at stage one with nothing running. Reporting that
    // as work in progress told the reader to wait for something that would
    // never happen.
    render(
      <IngestionStatus
        readiness="processing"
        ingestion={ingestion({ status: "awaiting_upload" })}
      />,
    );

    expect(screen.getByText(/Waiting for the video file/i)).toBeTruthy();
    expect(screen.queryByText(/Fetching the video/i)).toBeNull();
  });

  it("describes the stage while work is genuinely running", () => {
    render(
      <IngestionStatus
        readiness="processing"
        ingestion={ingestion({ status: "running", stage: "visual_analysis" })}
      />,
    );

    expect(screen.getByText(/Interpreting what is shown/i)).toBeTruthy();
    expect(screen.getByText(/step 7 of 12/i)).toBeTruthy();
    expect(screen.getByText("What you’ll get")).toBeTruthy();
    expect(screen.getByText("Steps remaining")).toBeTruthy();
    expect(screen.getByText("Technical details")).toBeTruthy();
  });

  it("surfaces a failure instead of an endless spinner", () => {
    render(
      <IngestionStatus
        readiness="processing"
        ingestion={ingestion({
          status: "failed",
          error: { code: "provider_unavailable", message: "A provider failed." },
          retryable: true,
        })}
      />,
    );

    // The cause, not a restatement of the heading it sits under.
    expect(screen.getByText("A provider failed.")).toBeTruthy();
    expect(screen.getByText(/can be retried/i)).toBeTruthy();
  });
});

describe("readinessLabel", () => {
  it("distinguishes an unfilled reservation from real progress", () => {
    expect(
      readinessLabel("processing", ingestion({ status: "awaiting_upload" })),
    ).toBe("Waiting for the file");
    expect(readinessLabel("processing", ingestion())).toBe("Processing");
    expect(readinessLabel("ready", ingestion({ status: "ready" }))).toBe("Ready");
  });

  it("calls a degraded version ready, and explains it separately", () => {
    // A published version answers questions. "Ready (partial)" spent the
    // reader's attention on a worry the badge had no room to explain; the
    // measured reason is shown beside it instead.
    expect(readinessLabel("degraded", null)).toBe("Ready");
  });

  it("names the cause when processing could not finish", () => {
    expect(readinessLabel("failed", null)).toBe("Could not be processed");
  });
});

describe("IngestionStatus for a published version", () => {
  it("states the reservation rather than hinting at one", () => {
    render(
      <IngestionStatus
        readiness="degraded"
        ingestion={ingestion({ status: "ready" })}
        notes={["The transcript covers 94% of the lecture."]}
      />,
    );

    expect(screen.getByText(/Ready to answer questions/i)).toBeTruthy();
    expect(
      screen.getByText("The transcript covers 94% of the lecture."),
    ).toBeTruthy();
  });
});
