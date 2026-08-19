import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SessionQuestions } from "@/components/read/session-questions";
import { ContinueSessions, sessionDetail } from "@/components/read/continue-sessions";
import type { SideChatThread } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";

function thread(id: string, title: string, anchors: Anchor[]): SideChatThread {
  return {
    conversation_id: id,
    parent_conversation_id: "session",
    title,
    anchors,
    turn_count: 1,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:00:00Z",
  };
}

const HERE = thread("a", "Why is accuracy the wrong measure?", [
  { kind: "document_page", anchor_id: "a1", book_id: 7, page: 108 },
]);
const ELSEWHERE = thread("b", "Is weak supervision worth it?", [
  { kind: "document_page", anchor_id: "b1", book_id: 7, page: 103 },
]);
const LOOSE = thread("c", "How does this compare to sampling?", []);

describe("SessionQuestions", () => {
  it("keeps a session's questions findable after their windows are closed", () => {
    // Otherwise a long session quietly loses the thing it was accumulating:
    // conversation history is off this surface entirely.
    const onOpen = vi.fn();
    render(
      <SessionQuestions
        threads={[HERE, ELSEWHERE]}
        openIds={[]}
        onOpen={onOpen}
        hereLabel="p. 108"
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    );

    expect(onOpen).toHaveBeenCalledWith(HERE);
  });

  it("leads with where the reader is", () => {
    render(
      <SessionQuestions
        threads={[ELSEWHERE, HERE]}
        openIds={[]}
        onOpen={vi.fn()}
        hereLabel="p. 108"
      />,
    );

    expect(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByRole("button", { name: /Is weak supervision worth it/ }),
    ).not.toHaveAttribute("aria-current");
  });

  it("says which questions already have a window open", () => {
    render(
      <SessionQuestions
        threads={[HERE, ELSEWHERE]}
        openIds={["a"]}
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getByText("open")).toBeInTheDocument();
  });

  it("names a question that anchors to nothing rather than leaving it blank", () => {
    render(
      <SessionQuestions threads={[LOOSE]} openIds={[]} onOpen={vi.fn()} />,
    );

    expect(screen.getByText("No anchor")).toBeInTheDocument();
  });

  it("explains itself before there is anything in it", () => {
    render(
      <SessionQuestions
        threads={[]}
        openIds={[]}
        onOpen={vi.fn()}
        hereLabel="p. 108"
      />,
    );

    expect(screen.getByText(/stay with the source between visits/)).toBeInTheDocument();
  });
});

describe("ContinueSessions", () => {
  it("offers the source back, at the place it was left", () => {
    render(
      <ContinueSessions
        entries={[
          {
            key: "s1",
            href: "/read/7",
            title: "Designing Machine Learning Systems",
            detail: sessionDetail("p. 112", 5),
          },
        ]}
      />,
    );

    expect(
      screen.getByRole("link", { name: /Designing Machine Learning Systems/ }),
    ).toHaveAttribute("href", "/read/7");
    expect(screen.getByText("p. 112 · 5 questions")).toBeInTheDocument();
  });

  it("claims no place in a source that was only opened", () => {
    // A session with no recorded position was never read past its opening, so
    // "p. 1" would be a place the reader never reached.
    expect(sessionDetail(null, 1)).toBe("1 question");
  });

  it("shows nothing at all when nothing has been started", () => {
    const { container } = render(<ContinueSessions entries={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});

describe("anchoredMoment", () => {
  it("finds the moment a lecture question names", async () => {
    // Kept when the timeline gutter went: the region opens a thread, and the
    // caller uses this to move the playhead with it.
    const { anchoredMoment } = await import("@/lib/anchors");

    expect(
      anchoredMoment([
        {
          kind: "lecture_moment",
          anchor_id: "m",
          video_id: "v",
          timestamp_ms: 724_000,
        },
      ]),
    ).toBe(724_000);
    expect(
      anchoredMoment([
        {
          kind: "lecture_stretch",
          anchor_id: "s",
          video_id: "v",
          start_ms: 700_000,
          end_ms: 750_000,
        },
      ]),
    ).toBe(700_000);
    expect(
      anchoredMoment([
        { kind: "document_page", anchor_id: "p", book_id: 7, page: 108 },
      ]),
    ).toBeNull();
  });
});
