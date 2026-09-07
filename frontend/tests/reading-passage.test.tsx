import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ReadingPassage } from "@/components/conversation/reading-passage";
import { TurnView } from "@/components/conversation/turn-view";
import type {
  ChatTurn,
  PassageResponse,
  PassageSegment,
  ReadingRef,
  TurnResult,
} from "@/lib/types";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch, API_BASE: "/api" }));

// Figures fetch their own bytes with a bearer token; the passage under test is
// about ordering and continuation, not about image loading.
vi.mock("@/hooks/use-authenticated-image", () => ({
  useAuthenticatedImage: () => ({ status: "ready", url: "blob:figure" }),
}));

const reading: ReadingRef = {
  book_id: 4,
  book_title: "Designing Data-Intensive Applications",
  node_id: 91,
  kind: "chapter",
  display_path: "Chapter 3. Storage and Retrieval",
  start_page: 69,
  end_page: 104,
  printed_start_page: 71,
  printed_end_page: 106,
  total_segments: 4,
  total_characters: 40,
  omitted_block_count: 3,
};

function segment(
  index: number,
  overrides: Partial<PassageSegment> = {},
): PassageSegment {
  return {
    index,
    kind: "text",
    node_id: 91,
    page: 69,
    printed_page: 71,
    text: `paragraph ${index}`,
    level: null,
    html: null,
    figure: null,
    ...overrides,
  };
}

function page(
  segments: PassageSegment[],
  offset: number,
  next: number | null,
): PassageResponse {
  return { reading, offset, next_offset: next, segments };
}

describe("ReadingPassage", () => {
  beforeEach(() => {
    apiFetch.mockReset();
  });

  it("names the scope, the page range as printed, and the book", async () => {
    apiFetch.mockResolvedValue(page([segment(0)], 0, null));

    render(<ReadingPassage reading={reading} />);

    await screen.findByText("Chapter 3. Storage and Retrieval");
    // The printed numbers, not the PDF pages: a reader jumping to the book
    // needs the number they will actually see on the page.
    expect(screen.getByText("pp. 71–106")).toBeInTheDocument();
    expect(
      screen.getByText("Designing Data-Intensive Applications"),
    ).toBeInTheDocument();
  });

  it("asks for the scope's own node so a chapter is not read as the book", async () => {
    apiFetch.mockResolvedValue(page([segment(0)], 0, null));

    render(<ReadingPassage reading={reading} />);

    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const path = apiFetch.mock.calls[0]?.[0] as string;
    expect(path).toContain("/books/4/passage");
    expect(path).toContain("node_id=91");
  });

  it("renders headings, prose, tables and figures in source order", async () => {
    apiFetch.mockResolvedValue(
      page(
        [
          segment(0, { kind: "heading", text: "Hash Indexes", level: 2 }),
          segment(1, { text: "An append-only log." }),
          segment(2, {
            kind: "table",
            text: "A",
            html: "<table><tr><td>A</td></tr></table>",
          }),
          segment(3, {
            kind: "figure",
            text: null,
            figure: {
              book_id: 4,
              node_id: 91,
              block_id: 12,
              page: 72,
              mime_type: "image/png",
              path: "Chapter 3 > Hash Indexes",
              caption: "A log-structured segment file",
              evidence_rank: null,
            },
          }),
        ],
        0,
        null,
      ),
    );

    const { container } = render(<ReadingPassage reading={reading} />);

    await screen.findByText("An append-only log.");
    expect(
      screen.getByRole("heading", { name: "Hash Indexes" }),
    ).toBeInTheDocument();
    expect(container.querySelector("table")).toBeTruthy();
    expect(
      screen.getByText("A log-structured segment file"),
    ).toBeInTheDocument();
  });

  it("continues from the offset the server gave rather than guessing one", async () => {
    apiFetch
      .mockResolvedValueOnce(page([segment(0), segment(1)], 0, 2))
      .mockResolvedValueOnce(page([segment(2, { text: "the third" })], 2, null));

    render(<ReadingPassage reading={reading} />);

    fireEvent.click(await screen.findByRole("button", { name: /Continue reading/ }));

    await screen.findByText("the third");
    expect(apiFetch.mock.calls[1]?.[0]).toContain("offset=2");
    // Earlier installments stay on screen: continuing extends the passage, it
    // does not page through it.
    expect(screen.getByText("paragraph 0")).toBeInTheDocument();
  });

  it("stops offering to continue at the end, and says so", async () => {
    apiFetch.mockResolvedValue(page([segment(0)], 0, null));

    render(<ReadingPassage reading={reading} />);

    await screen.findByText("paragraph 0");
    expect(
      screen.queryByRole("button", { name: /Continue reading/ }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/End of chapter/)).toBeInTheDocument();
    // The exception to "verbatim" is stated rather than left to be noticed.
    expect(screen.getByText(/omitted/)).toBeInTheDocument();
  });

  it("keeps what was already read when continuing fails", async () => {
    apiFetch
      .mockResolvedValueOnce(page([segment(0)], 0, 1))
      .mockRejectedValueOnce(new Error("Request failed (502)"));

    render(<ReadingPassage reading={reading} />);

    fireEvent.click(await screen.findByRole("button", { name: /Continue reading/ }));

    await screen.findByText("Request failed (502)");
    expect(screen.getByText("paragraph 0")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("sets list items as one list, and a page turn ends it", async () => {
    apiFetch.mockResolvedValue(
      page(
        [
          segment(0, { kind: "list_item", text: "WD1 a thing", printed_page: 71 }),
          segment(1, { kind: "list_item", text: "WD2 not a thing", printed_page: 71 }),
          segment(2, { kind: "list_item", text: "WD3 overleaf", printed_page: 72 }),
        ],
        0,
        null,
      ),
    );

    const { container } = render(<ReadingPassage reading={reading} />);

    await screen.findByText("WD1 a thing");
    const lists = container.querySelectorAll("ul");
    expect(lists).toHaveLength(2);
    expect(lists[0]?.querySelectorAll("li")).toHaveLength(2);
    expect(lists[1]?.querySelectorAll("li")).toHaveLength(1);
  });

  it("sets a caption and a formula apart from the prose", async () => {
    apiFetch.mockResolvedValue(
      page(
        [
          segment(0, { kind: "caption", text: "Figure 1: a thing." }),
          segment(1, { kind: "formula", text: "E = mc^2" }),
        ],
        0,
        null,
      ),
    );

    const { container } = render(<ReadingPassage reading={reading} />);

    await screen.findByText("Figure 1: a thing.");
    // The formula is stored as text, not markup, so it is set monospaced and
    // scrollable rather than typeset as maths that was never there.
    expect(container.querySelector("pre")?.textContent).toBe("E = mc^2");
  });

  it("marks where the page turns, once per turn", async () => {
    apiFetch.mockResolvedValue(
      page(
        [
          segment(0, { printed_page: 71 }),
          segment(1, { printed_page: 71 }),
          segment(2, { printed_page: 72 }),
        ],
        0,
        null,
      ),
    );

    render(<ReadingPassage reading={reading} />);

    await screen.findByText("paragraph 2");
    expect(screen.getAllByText(/^p\. 7/)).toHaveLength(1);
    expect(screen.getByText("p. 72")).toBeInTheDocument();
  });
});

function verbatimTurn(): ChatTurn {
  const result: TurnResult = {
    question: "read chapter 3 in full",
    answer: "**Chapter 3. Storage and Retrieval**",
    route: "verbatim_reading",
    history_dependency: "independent",
    standalone_query: "Read Chapter 3. Storage and Retrieval verbatim.",
    resolved_scope: null,
    evidence: [],
    citations: [],
    figures: [],
    outline_node_ids: [],
    reading,
    outcome: "answer",
    retrieval_mode: null,
    warnings: [],
    answer_archetype: null,
    response_depth: null,
    routing_reason: null,
    prompt_profile_version: null,
    side_context: null,
  };
  return {
    id: "turn-1",
    question: "read chapter 3 in full",
    answer: result.answer,
    status: "complete",
    result,
    error: null,
    turnIndex: 0,
  };
}

describe("a verbatim turn in the conversation", () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue(page([segment(0)], 0, null));
  });

  it("renders the passage under the turn's one-line header", async () => {
    render(
      <TurnView
        turn={verbatimTurn()}
        isLast
        canRetry={false}
        onRetry={() => {}}
      />,
    );

    await screen.findByText("paragraph 0");
    expect(
      screen.getByRole("region", {
        name: /Chapter 3\. Storage and Retrieval, read in full/,
      }),
    ).toBeInTheDocument();
  });
});
