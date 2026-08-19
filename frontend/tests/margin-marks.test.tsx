import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MarginMarks, anchoredPage, marksFor } from "@/components/read/margin-marks";
import type { SideChatThread } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";

function thread(
  id: string,
  title: string,
  anchors: Anchor[],
): SideChatThread {
  return {
    conversation_id: id,
    parent_conversation_id: "session",
    title,
    anchors,
    turn_count: 1,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
  };
}

const ON_PAGE = thread("a", "Why is accuracy the wrong measure?", [
  { kind: "document_passage", anchor_id: "a1", book_id: 7, page: 108, selected_text: "accuracy" },
]);
const ELSEWHERE = thread("b", "Is weak supervision worth it?", [
  { kind: "document_page", anchor_id: "b1", book_id: 7, page: 103 },
]);
const UNANCHORED = thread("c", "How does this compare to sampling?", []);

describe("marksFor", () => {
  it("keeps only the questions that name a place", () => {
    // A question asked with the page chip off names no page, so there is no
    // margin for it to be in.
    expect(marksFor([ON_PAGE, ELSEWHERE, UNANCHORED])).toEqual([
      { thread: ON_PAGE, page: 108 },
      { thread: ELSEWHERE, page: 103 },
    ]);
  });

  it("reads the page from either kind of source anchor", () => {
    expect(anchoredPage(ON_PAGE.anchors)).toBe(108);
    expect(anchoredPage(ELSEWHERE.anchors)).toBe(103);
    expect(anchoredPage([])).toBeNull();
  });

  it("ignores a quote anchor, which names an answer rather than a page", () => {
    const quoted = thread("d", "What did that mean?", [
      { anchor_id: "d1", parent_turn_index: 0, quoted_text: "the gap compounds" },
    ]);

    expect(marksFor([quoted])).toEqual([]);
  });
});

describe("MarginMarks", () => {
  it("shows nothing at all before anything has been asked", () => {
    const { container } = render(
      <MarginMarks marks={[]} page={108} onOpen={vi.fn()} onGoToPage={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("reopens the question left on this page", () => {
    // The mark is the way back into a thread the reader closed, which is what
    // makes the session an artifact rather than a transcript.
    const onOpen = vi.fn();
    render(
      <MarginMarks
        marks={marksFor([ON_PAGE, ELSEWHERE])}
        page={108}
        onOpen={onOpen}
        onGoToPage={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    );

    expect(onOpen).toHaveBeenCalledWith(ON_PAGE);
  });

  it("turns to the page a question elsewhere was asked on", () => {
    const onGoToPage = vi.fn();
    render(
      <MarginMarks
        marks={marksFor([ON_PAGE, ELSEWHERE])}
        page={108}
        onOpen={vi.fn()}
        onGoToPage={onGoToPage}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Is weak supervision worth it/ }),
    );

    expect(onGoToPage).toHaveBeenCalledWith(103);
  });

  it("says the page is unmarked rather than looking empty", () => {
    render(
      <MarginMarks
        marks={marksFor([ELSEWHERE])}
        page={108}
        onOpen={vi.fn()}
        onGoToPage={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Nothing asked on this page yet."),
    ).toBeInTheDocument();
  });
});
