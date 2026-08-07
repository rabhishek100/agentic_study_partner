import { fireEvent, render, screen } from "@testing-library/react";
import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskSelection } from "@/components/side-chat/ask-selection";

function Harness({ onAsk }: { onAsk: (index: number, text: string) => void }) {
  const container = useRef<HTMLDivElement | null>(null);
  return (
    <>
      <div ref={container}>
        <article data-turn-index="3">
          <div data-answer="">
            <p data-testid="answer-a">
              Training-serving skew arises when features differ between training
              and serving.
            </p>
          </div>
          {/* Reference chrome, not a passage. */}
          <p data-testid="reference-label">8 Advanced Practice</p>
        </article>
        <article data-turn-index="4">
          <div data-answer="">
            <p data-testid="answer-b">
              The gap compounds once the pipelines diverge.
            </p>
          </div>
        </article>
        {/* A turn the server never recorded carries no index to anchor to. */}
        <article>
          <div data-answer="">
            <p data-testid="answer-unrecorded">A stopped turn.</p>
          </div>
        </article>
      </div>
      <p data-testid="outside">Text outside the conversation column.</p>
      <AskSelection container={container} onAsk={onAsk} />
    </>
  );
}

/**
 * jsdom implements no layout, so `Range.getBoundingClientRect` does not exist.
 * The component positions itself from it, so the tests supply one.
 */
const baseRect = {
  left: 400,
  top: 300,
  right: 600,
  bottom: 320,
  width: 200,
  height: 20,
  x: 400,
  y: 300,
  toJSON: () => ({}),
} as DOMRect;

let rangeRect = baseRect;

Object.defineProperty(Range.prototype, "getBoundingClientRect", {
  configurable: true,
  value: () => rangeRect,
});

function selectWithin(testId: string, endTestId = testId) {
  const start = screen.getByTestId(testId).firstChild!;
  const end = screen.getByTestId(endTestId).firstChild!;
  const range = document.createRange();
  range.setStart(start, 0);
  range.setEnd(end, end.textContent!.length);
  const selection = document.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  fireEvent.pointerUp(document);
}

beforeEach(() => {
  rangeRect = baseRect;
});

afterEach(() => {
  document.getSelection()?.removeAllRanges();
  vi.restoreAllMocks();
});

const ask = () => screen.queryByRole("button", { name: "Ask about this" });

describe("AskSelection", () => {
  it("offers nothing until something is selected", () => {
    render(<Harness onAsk={vi.fn()} />);

    expect(ask()).not.toBeInTheDocument();
  });

  it("offers to ask about a highlighted passage", () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);

    selectWithin("answer-a");

    expect(ask()).toBeInTheDocument();
    fireEvent.click(ask()!);
    expect(onAsk).toHaveBeenCalledWith(
      3,
      expect.stringContaining("Training-serving skew arises"),
    );
  });

  it("anchors to the turn the selection is in, not the first one", () => {
    const onAsk = vi.fn();
    render(<Harness onAsk={onAsk} />);

    selectWithin("answer-b");
    fireEvent.click(ask()!);

    expect(onAsk).toHaveBeenCalledWith(4, expect.stringContaining("compounds"));
  });

  it("refuses a selection spanning two turns rather than guessing one", () => {
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("answer-a", "answer-b");

    expect(ask()).not.toBeInTheDocument();
  });

  it("ignores a selection in a turn the server never recorded", () => {
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("answer-unrecorded");

    expect(ask()).not.toBeInTheDocument();
  });

  it("ignores a selection of reference chrome rather than a passage", () => {
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("reference-label");

    expect(ask()).not.toBeInTheDocument();
  });

  it("ignores a selection outside the conversation", () => {
    render(<Harness onAsk={vi.fn()} />);

    selectWithin("outside");

    expect(ask()).not.toBeInTheDocument();
  });

  it("ignores a stray click-drag of a character or two", () => {
    render(<Harness onAsk={vi.fn()} />);

    const node = screen.getByTestId("answer-a").firstChild!;
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, 2);
    const selection = document.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    fireEvent.pointerUp(document);

    expect(ask()).not.toBeInTheDocument();
  });

  it("goes away when the selection collapses", () => {
    render(<Harness onAsk={vi.fn()} />);
    selectWithin("answer-a");
    expect(ask()).toBeInTheDocument();

    document.getSelection()!.removeAllRanges();
    fireEvent(document, new Event("selectionchange"));

    expect(ask()).not.toBeInTheDocument();
  });

  it("goes away on Escape", () => {
    render(<Harness onAsk={vi.fn()} />);
    selectWithin("answer-a");

    fireEvent.keyDown(document, { key: "Escape" });

    expect(ask()).not.toBeInTheDocument();
  });

  it("clears the selection once it has been asked about", () => {
    render(<Harness onAsk={vi.fn()} />);
    selectWithin("answer-a");

    fireEvent.click(ask()!);

    expect(document.getSelection()?.toString()).toBe("");
    expect(ask()).not.toBeInTheDocument();
  });

  it("sits above the passage, and below it when there is no room above", () => {
    render(<Harness onAsk={vi.fn()} />);
    selectWithin("answer-a");
    const above = ask()!.style.top;

    // A selection near the top of the viewport has nowhere to put a button.
    rangeRect = { ...baseRect, top: 4, bottom: 24, y: 4 } as DOMRect;
    fireEvent.keyDown(document, { key: "Escape" });
    selectWithin("answer-a");

    expect(ask()!.style.top).not.toBe(above);
    expect(Number.parseFloat(ask()!.style.top)).toBeGreaterThan(24);
  });
});
