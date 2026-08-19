import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PageSelectionPopover } from "@/components/read/page-selection";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch, API_BASE: "/api" }));

function Harness({
  onAsk,
  sessionId = "session-1",
}: {
  onAsk: (text: string, question?: string) => void;
  sessionId?: string | null;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  return (
    <>
      <div ref={container}>
        {/* pdf.js positions the text layer over the canvas; only its spans are
            the book's words. */}
        <div className="react-pdf__Page__textContent">
          <span data-testid="passage">
            The measurement that hides this is accuracy.
          </span>
        </div>
        {/* Chrome inside the viewer, not the book. */}
        <p data-testid="toolbar">108 / 386</p>
      </div>
      <p data-testid="outside">Text outside the document.</p>
      <PageSelectionPopover
        container={container}
        sessionId={sessionId}
        bookId={7}
        page={108}
        onAsk={onAsk}
      />
    </>
  );
}

/** jsdom implements no layout, so the component's positioning source is faked. */
Object.defineProperty(Range.prototype, "getBoundingClientRect", {
  configurable: true,
  value: () =>
    ({
      left: 400,
      top: 300,
      right: 600,
      bottom: 320,
      width: 200,
      height: 20,
      x: 400,
      y: 300,
      toJSON: () => ({}),
    }) as DOMRect,
});

function selectWithin(testId: string) {
  const node = screen.getByTestId(testId).firstChild!;
  const range = document.createRange();
  range.setStart(node, 0);
  range.setEnd(node, node.textContent!.length);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  fireEvent.pointerUp(document);
}

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({
    matched: true,
    label: "p. 108 · 4.3 Class imbalance",
    passage_count: 2,
  });
});

afterEach(() => {
  document.getSelection()?.removeAllRanges();
});

const popover = () =>
  screen.queryByRole("dialog", { name: "Ask about the selected passage" });

describe("PageSelectionPopover", () => {
  it("offers nothing until something is selected", () => {
    render(<Harness onAsk={vi.fn()} />);

    expect(popover()).not.toBeInTheDocument();
  });

  it("offers to ask about a highlighted passage of the book", async () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);

    selectWithin("passage");

    expect(popover()).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ask about this" }));
    expect(onAsk).toHaveBeenCalledWith(
      expect.stringContaining("The measurement that hides this"),
      undefined,
    );
  });

  it("ignores a selection that is not the book's words", () => {
    // Only pdf.js's text layer carries text belonging to the book. Selecting
    // the page counter is not selecting a passage.
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("toolbar");

    expect(popover()).not.toBeInTheDocument();
  });

  it("ignores a selection outside the document entirely", () => {
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("outside");

    expect(popover()).not.toBeInTheDocument();
  });

  it("says what the selection resolves to before anything is asked", async () => {
    // Resolving before asking is the point: the reader learns whether their
    // highlight is citable before committing a question to it.
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("passage");

    await waitFor(() =>
      expect(
        screen.getByText(/Matches p\. 108 · 4\.3 Class imbalance — 2 passages/),
      ).toBeInTheDocument(),
    );
    const [path, options] = apiFetch.mock.calls.at(-1)!;
    expect(path).toBe("/reading-sessions/session-1/anchors/resolve");
    expect(JSON.parse((options as RequestInit).body as string)).toEqual({
      anchor: {
        kind: "document_passage",
        book_id: 7,
        page: 108,
        selected_text: "The measurement that hides this is accuracy.",
      },
    });
  });

  it("says plainly when the selection matches nothing", async () => {
    // A miss is a designed state, not an error: the words still go in as
    // context and the page still grounds the answer.
    apiFetch.mockResolvedValue({
      matched: false,
      label: "p. 108",
      passage_count: 1,
    });
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("passage");

    await waitFor(() =>
      expect(
        screen.getByText(/No match in the book's text/),
      ).toBeInTheDocument(),
    );
  });

  it("carries a preset question instead of opening an empty window", () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);

    selectWithin("passage");
    fireEvent.click(
      screen.getByRole("button", { name: "Explain in simpler terms" }),
    );

    expect(onAsk).toHaveBeenCalledWith(
      expect.stringContaining("The measurement that hides this"),
      "Explain this passage in simpler terms.",
    );
  });

  it("closes on Escape without asking anything", () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);

    selectWithin("passage");
    fireEvent.keyDown(document, { key: "Escape" });

    expect(popover()).not.toBeInTheDocument();
    expect(onAsk).not.toHaveBeenCalled();
  });
});
