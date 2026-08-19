import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  SessionRecap,
  recapOf,
  type Chapter,
} from "@/components/read/session-recap";
import type { Anchor } from "@/lib/types";

const apiFetch = vi.hoisted(() => vi.fn());
const push = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch, API_BASE: "/api" }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const CHAPTERS: Chapter[] = [
  { node_id: 41, title: "4.1 Sampling", start_page: 99, end_page: 102 },
  { node_id: 43, title: "4.3 Class imbalance", start_page: 103, end_page: 118 },
];

function page(number: number): Anchor {
  return {
    kind: "document_page",
    anchor_id: `a${number}`,
    book_id: 7,
    page: number,
  };
}

beforeEach(() => {
  apiFetch.mockReset();
  push.mockReset();
});

describe("recapOf", () => {
  it("reports the span of the session and where it concentrated", () => {
    const recap = recapOf([[page(100)], [page(108)], [page(112)]], CHAPTERS);

    expect(recap.questionCount).toBe(3);
    expect(recap.firstPage).toBe(100);
    expect(recap.lastPage).toBe(112);
    expect(recap.busiest?.node_id).toBe(43);
  });

  it("has nothing to recap before anything is asked", () => {
    expect(recapOf([], CHAPTERS)).toEqual({
      questionCount: 0,
      firstPage: null,
      lastPage: null,
      busiest: null,
    });
  });

  it("ignores questions that name no page", () => {
    // A question asked with the chip off covers no part of the book, so it
    // cannot pull the recap toward a section it never touched.
    const unanchored: Anchor[] = [];
    const quoted: Anchor[] = [
      { anchor_id: "q", parent_turn_index: 0, quoted_text: "the gap" },
    ];

    expect(recapOf([unanchored, quoted], CHAPTERS).questionCount).toBe(0);
  });

  it("survives a page the outline does not cover", () => {
    // Front matter, an appendix, a page between sections: it still counts
    // toward the span, it just cannot vote for a section.
    const recap = recapOf([[page(3)], [page(108)]], CHAPTERS);

    expect(recap.firstPage).toBe(3);
    expect(recap.busiest?.node_id).toBe(43);
  });
});

describe("SessionRecap", () => {
  it("says nothing when the session has no questions", () => {
    const { container } = render(
      <SessionRecap recap={recapOf([], CHAPTERS)} bookId={7} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("describes the session in the reader's terms", () => {
    render(
      <SessionRecap
        recap={recapOf([[page(104)], [page(108)]], CHAPTERS)}
        bookId={7}
      />,
    );

    expect(
      screen.getByText(/2 questions across pp\. 104–108, mostly in 4\.3 Class imbalance/),
    ).toBeInTheDocument();
  });

  it("names a single page as a page rather than a span", () => {
    render(<SessionRecap recap={recapOf([[page(108)]], CHAPTERS)} bookId={7} />);

    expect(screen.getByText(/1 question across p\. 108/)).toBeInTheDocument();
  });

  it("turns the session into a deck through the generator that exists", async () => {
    // Scoped by the chapter the session concentrated in, rather than by a
    // second generator that knows about sessions.
    apiFetch.mockResolvedValueOnce({ job_id: "job-1" });
    render(
      <SessionRecap
        recap={recapOf([[page(108)], [page(112)]], CHAPTERS)}
        bookId={7}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /Make a deck from 4\.3 Class imbalance/ }),
    );

    expect(apiFetch).toHaveBeenCalledWith("/decks", {
      method: "POST",
      body: JSON.stringify({
        source_kind: "book",
        generation_mode: "topic_generated",
        book_id: 7,
        node_id: 43,
      }),
    });
    await waitFor(() => expect(push).toHaveBeenCalledWith("/decks"));
  });

  it("stays on the page and says so when the deck cannot be queued", async () => {
    apiFetch.mockRejectedValueOnce(new Error("nope"));
    render(<SessionRecap recap={recapOf([[page(108)]], CHAPTERS)} bookId={7} />);

    await userEvent.click(screen.getByRole("button", { name: /Make a deck/ }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
