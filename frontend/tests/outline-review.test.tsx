import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { OutlineReviewEditor } from "@/components/outline-review";
import { apiFetch } from "@/lib/api";
import type { IngestionJob, OutlineReview } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
}));

const review: OutlineReview = {
  job_id: "job-1",
  status: "needs_toc_review",
  page_count: 20,
  outline_source: "deterministic_proposal",
  proposer_version: "outline-proposer-v1",
  reasons: ["invalid_destinations"],
  warnings: [],
  entries: [
    { level: 1, title: "Chapter 1", page: 1 },
    { level: 2, title: "First section", page: 3 },
  ],
};

const queued = {
  job_id: "job-1",
  status: "queued",
  stage: "preflight",
} as IngestionJob;

describe("OutlineReviewEditor", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
  });

  it("loads, edits, and confirms the proposed hierarchy", async () => {
    const user = userEvent.setup();
    const onConfirmed = vi.fn();
    vi.mocked(apiFetch)
      .mockResolvedValueOnce(review)
      .mockResolvedValueOnce(queued);

    render(
      <OutlineReviewEditor jobId="job-1" onConfirmed={onConfirmed} />,
    );

    await user.click(
      await screen.findByRole("button", { name: "Review 2 headings" }),
    );
    const title = screen.getByLabelText("Title for heading 2");
    await user.clear(title);
    await user.type(title, "Corrected section");
    await user.click(
      screen.getByRole("button", { name: "Confirm and continue" }),
    );

    await waitFor(() => expect(onConfirmed).toHaveBeenCalledWith(queued));
    expect(apiFetch).toHaveBeenNthCalledWith(
      2,
      "/ingestions/job-1/toc-confirmation",
      {
        method: "POST",
        body: JSON.stringify({
          entries: [
            { level: 1, title: "Chapter 1", page: 1 },
            { level: 2, title: "Corrected section", page: 3 },
          ],
        }),
      },
    );
  });
});
