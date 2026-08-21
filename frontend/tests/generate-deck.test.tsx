import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { GenerateDeck } from "@/components/decks/generate-deck";
import { apiFetch } from "@/lib/api";
import type { DeckJob } from "@/lib/deck-types";

vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

const queuedPaperJob = { job_id: "paper-job" } as DeckJob;

describe("manual paper deck generation", () => {
  beforeEach(() => {
    vi.mocked(apiFetch).mockReset();
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/books") return { books: [] };
      if (path === "/papers") {
        return {
          books: [
            {
              book_id: 42,
              title: "Attention Is All You Need",
              author: null,
              page_count: 15,
              ready_at: "2026-08-21T00:00:00Z",
              chunk_count: 12,
              embedding_count: 12,
              retrieval_complete: true,
              document_type: "paper",
            },
          ],
        };
      }
      if (path === "/videos") return { videos: [] };
      if (path === "/decks") return queuedPaperJob;
      throw new Error(`Unexpected API path: ${path}`);
    });
  });

  it("submits one complete paper without a chapter node", async () => {
    const user = userEvent.setup();
    const onQueued = vi.fn();
    render(<GenerateDeck onQueued={onQueued} />);

    await user.click(screen.getByRole("button", { name: "New deck" }));
    await user.click(screen.getByRole("button", { name: "Paper" }));

    expect(
      screen.getByText(/each numbered set covers one complete paper/i),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Chapter")).toBeNull();
    expect(screen.queryByText("Question source")).toBeNull();

    await user.click(screen.getByLabelText("Paper"));
    await user.click(
      await screen.findByRole("option", {
        name: "Attention Is All You Need",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Generate" }));

    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/decks", {
        method: "POST",
        body: JSON.stringify({
          source_kind: "book",
          generation_mode: "topic_generated",
          book_id: 42,
          node_id: null,
        }),
      }),
    );
    expect(onQueued).toHaveBeenCalledWith(queuedPaperJob);
  });
});
