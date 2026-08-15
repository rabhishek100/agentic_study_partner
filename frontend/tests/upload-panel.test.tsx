import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UploadPanel } from "@/components/upload-panel";
import { apiFetch } from "@/lib/api";
import type { IngestionJob, OutlineReview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

function job(overrides: Partial<IngestionJob>): IngestionJob {
  return {
    job_id: "job-1",
    status: "ready",
    stage: null,
    original_filename: "paper.pdf",
    progress: { completed: 1, total: 1, unit: "chunks", percent: 100 },
    attempt: 1,
    max_attempts: 3,
    retryable: false,
    page_count: 10,
    book_id: 1,
    document_type: "paper",
    error: null,
    cancellation_requested: false,
    driveable: true,
    timing: {
      percent: 100,
      elapsed_seconds: 60,
      estimated_total_seconds: 60,
      estimated_remaining_seconds: 0,
      overrunning: false,
      stages: [],
      awaiting_input: false,
      awaiting_input_seconds: 0,
    },
    created_at: "2026-08-14T00:00:00Z",
    started_at: "2026-08-14T00:00:01Z",
    updated_at: "2026-08-14T00:01:00Z",
    completed_at: "2026-08-14T00:01:00Z",
    ...overrides,
  };
}

const waitingReview = job({
  job_id: "review-job",
  status: "needs_toc_review",
  stage: "propose_toc",
  original_filename: "older-paper.pdf",
  progress: { completed: 0, total: 141, unit: "headings", percent: 0 },
  book_id: null,
  timing: {
    percent: 75,
    elapsed_seconds: 900,
    estimated_total_seconds: 1200,
    estimated_remaining_seconds: null,
    overrunning: false,
    stages: [],
    awaiting_input: true,
    awaiting_input_seconds: 300,
  },
  completed_at: null,
});

const proposal: OutlineReview = {
  job_id: "review-job",
  status: "needs_toc_review",
  page_count: 519,
  outline_source: "transcribed_headings",
  proposer_version: "transcribed-headings-v1",
  reasons: ["ocr_transcription"],
  warnings: [],
  entries: Array.from({ length: 141 }, (_, index) => ({
    level: 1,
    title: `Heading ${index + 1}`,
    page: index + 1,
  })),
};

describe("UploadPanel reattachment", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("finds an older review job after newer completed bulk uploads", async () => {
    const newerReady = Array.from({ length: 5 }, (_, index) =>
      job({ job_id: `ready-${index}`, original_filename: `newer-${index}.pdf` }),
    );
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/ingestions/limits") {
        return {
          maximum_bytes: 52_428_800,
          maximum_pages: 1000,
          allowed_content_types: ["application/pdf"],
        };
      }
      if (path === "/ingestions?limit=100") {
        return { jobs: [...newerReady, waitingReview] };
      }
      if (path === "/ingestions/review-job/toc-proposal") return proposal;
      throw new Error(`Unexpected API path: ${path}`);
    });

    render(
      <UploadPanel
        documentType="paper"
        onBookReady={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "Add a paper" })).toBeTruthy();

    expect(
      await screen.findByRole("button", { name: "Review 141 headings" }),
    ).toBeTruthy();
    expect(apiFetch).toHaveBeenCalledWith("/ingestions?limit=100");
  });
});
