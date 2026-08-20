import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  SessionQuestions,
  type SessionQuestionsProps,
} from "@/components/read/session-questions";
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

const QUOTED = thread("d", "What does durable mean here?", [
  {
    kind: "document_passage",
    anchor_id: "d1",
    book_id: 7,
    page: 24,
    selected_text: "a durable component, stored in memory",
  },
]);

function panel(props: Partial<SessionQuestionsProps> = {}) {
  return (
    <SessionQuestions
      threads={[HERE, ELSEWHERE]}
      detachedIds={[]}
      selectedId={null}
      onOpen={vi.fn()}
      onBack={vi.fn()}
      {...props}
    />
  );
}

describe("SessionQuestions", () => {
  it("keeps a session's questions findable after their threads are put away", () => {
    // Otherwise a long session quietly loses the thing it was accumulating:
    // conversation history is off this surface entirely.
    const onOpen = vi.fn();
    render(panel({ onOpen, hereLabel: "p. 108" }));

    fireEvent.click(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    );

    expect(onOpen).toHaveBeenCalledWith(HERE);
  });

  it("leads with where the reader is", () => {
    render(panel({ threads: [ELSEWHERE, HERE], hereLabel: "p. 108" }));

    expect(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    ).toHaveAttribute("aria-current", "true");
    expect(
      screen.getByRole("button", { name: /Is weak supervision worth it/ }),
    ).not.toHaveAttribute("aria-current");
  });

  it("leads a row's spoken name with the question, not its anchor", () => {
    // Read in source order the row announced "p. 108 detached why is
    // accuracy…", which buries the one thing that tells two rows apart.
    render(panel({ detachedIds: ["a"] }));

    expect(
      screen.getByRole("button", {
        name: "Why is accuracy the wrong measure?. Anchored to page 108. In a floating window",
      }),
    ).toBeInTheDocument();
  });

  it("says which questions stepped out into a window of their own", () => {
    render(panel({ detachedIds: ["a"] }));

    expect(screen.getByText("Detached")).toBeInTheDocument();
  });

  it("names a question that anchors to nothing rather than leaving it blank", () => {
    render(panel({ threads: [LOOSE] }));

    expect(screen.getByText("No anchor")).toBeInTheDocument();
  });

  it("shows the words a selection anchored to, not just its page", () => {
    render(panel({ threads: [QUOTED] }));

    expect(
      screen.getByText(/a durable component, stored in memory/),
    ).toBeInTheDocument();
  });

  it("explains itself before there is anything in it", () => {
    render(panel({ threads: [], hereLabel: "p. 108" }));

    expect(
      screen.getByText(/stay with the source between visits/),
    ).toBeInTheDocument();
  });

  it("shows the opened thread in place of the list, and the list back again", () => {
    const { rerender } = render(
      panel({ detail: <p>the answer</p>, selectedId: null }),
    );

    // The thread is mounted from the start — a thread that unmounted when the
    // list showed would abort the answer it is generating.
    expect(screen.getByText("the answer")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    ).toBeVisible();

    rerender(panel({ detail: <p>the answer</p>, selectedId: "a" }));

    expect(screen.getByText("the answer")).toBeVisible();
    // Gone from the accessibility tree as well as from view: the list is
    // hidden rather than merely covered, so Tab cannot wander into it.
    expect(
      screen.queryByRole("button", { name: /Why is accuracy the wrong measure/ }),
    ).toBeNull();
    // The row itself, not the thread header that now carries the same words.
    expect(
      screen.getByText("Is weak supervision worth it?"),
    ).not.toBeVisible();
  });

  it("returns focus to the row that opened a thread", () => {
    // The swap is a re-render inside one container, so nothing manages focus
    // for us: hiding the view holding the focused element drops focus to the
    // body, and the next Tab restarts at the top of the document.
    const { rerender } = render(panel({ selectedId: "a" }));

    rerender(panel({ selectedId: null }));

    expect(
      screen.getByRole("button", { name: /Why is accuracy the wrong measure/ }),
    ).toHaveFocus();
  });

  it("offers a thread a window of its own, named by the question", () => {
    const onDetach = vi.fn();
    render(panel({ selectedId: "a", onDetach }));

    fireEvent.click(
      screen.getByRole("button", {
        name: 'Open \u201cWhy is accuracy the wrong measure?\u201d in a floating window',
      }),
    );

    expect(onDetach).toHaveBeenCalledWith(HERE);
  });

  it("offers no detaching where a window could not be placed", () => {
    // Below the floating threshold there is no room for one, and the panel is
    // the better home anyway.
    render(panel({ selectedId: "a" }));

    expect(
      screen.queryByRole("button", { name: /in a floating window/ }),
    ).not.toBeInTheDocument();
  });

  it("says on its own surface when the session is locked to its source", () => {
    // The lock persists between visits and produces refusals that read like
    // bad retrieval. The control may live in the menu; its engaged state may
    // not.
    render(panel({ lockLabel: "book" }));

    expect(screen.getByText("book")).toBeInTheDocument();
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
