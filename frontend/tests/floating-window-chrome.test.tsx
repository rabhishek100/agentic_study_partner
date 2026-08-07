import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FloatingWindow } from "@/components/side-chat/floating-window";
import { DEFAULT_HEIGHT, DEFAULT_WIDTH } from "@/lib/floating-window";

const RECT = { x: 200, y: 120, width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT };

type WindowProps = React.ComponentProps<typeof FloatingWindow>;

function handlers() {
  return {
    onRectChange: vi.fn(),
    onMinimize: vi.fn(),
    onClose: vi.fn(),
    onFocus: vi.fn(),
  };
}

function renderWindow(overrides: Partial<WindowProps> = {}) {
  const mocks = handlers();
  render(
    <FloatingWindow
      title="the gap compounds"
      rect={RECT}
      zIndex={30}
      {...mocks}
      {...overrides}
    >
      <p>window body</p>
    </FloatingWindow>,
  );
  return mocks;
}

const moveHandle = () =>
  screen.getByRole("button", { name: /^Move the the gap compounds side chat/ });
const resizeHandle = () =>
  screen.getByRole("button", { name: /^Resize the the gap compounds side chat/ });

afterEach(() => {
  document.body.style.removeProperty("user-select");
  document.body.style.removeProperty("cursor");
});

describe("FloatingWindow", () => {
  it("is a labelled, non-modal dialog so several can be used at once", () => {
    renderWindow();

    const dialog = screen.getByRole("dialog", { name: "the gap compounds" });
    expect(dialog).toHaveAttribute("aria-modal", "false");
  });

  it("moves with the arrow keys", () => {
    const props = renderWindow();

    fireEvent.keyDown(moveHandle(), { key: "ArrowRight" });

    expect(props.onRectChange).toHaveBeenCalledWith(
      expect.objectContaining({ x: RECT.x + 16, y: RECT.y }),
    );
  });

  it("takes larger keyboard steps with shift held", () => {
    const props = renderWindow();

    fireEvent.keyDown(moveHandle(), { key: "ArrowDown", shiftKey: true });

    expect(props.onRectChange).toHaveBeenCalledWith(
      expect.objectContaining({ y: RECT.y + 64 }),
    );
  });

  it("resizes with the arrow keys from its own handle", () => {
    const props = renderWindow();

    fireEvent.keyDown(resizeHandle(), { key: "ArrowRight" });

    expect(props.onRectChange).toHaveBeenCalledWith(
      expect.objectContaining({ width: DEFAULT_WIDTH + 16 }),
    );
  });

  it("ignores keys that are not arrows", () => {
    const props = renderWindow();

    fireEvent.keyDown(moveHandle(), { key: "a" });

    expect(props.onRectChange).not.toHaveBeenCalled();
  });

  it("moves with the pointer", () => {
    const props = renderWindow();

    fireEvent.pointerDown(moveHandle(), {
      button: 0,
      pointerId: 1,
      clientX: 300,
      clientY: 300,
    });
    fireEvent.pointerMove(window, {
      pointerId: 1,
      clientX: 340,
      clientY: 330,
    });

    expect(props.onRectChange).toHaveBeenCalledWith(
      expect.objectContaining({ x: RECT.x + 40, y: RECT.y + 30 }),
    );
  });

  it("keeps dragging after a re-render hands it a new callback", () => {
    // The bug this covers: the pointer listeners used to live in an effect
    // keyed on `onRectChange`, and the layer builds a new callback every
    // render. The first move re-rendered, the effect tore down, and its
    // cleanup dropped the drag — so windows could not be dragged at all.
    const first = vi.fn();
    const { rerender } = render(
      <FloatingWindow
        title="the gap compounds"
        rect={RECT}
        zIndex={30}
        onRectChange={first}
        onMinimize={vi.fn()}
        onClose={vi.fn()}
        onFocus={vi.fn()}
      >
        <p>body</p>
      </FloatingWindow>,
    );

    fireEvent.pointerDown(moveHandle(), {
      button: 0,
      pointerId: 1,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 120, clientY: 100 });
    expect(first).toHaveBeenCalledTimes(1);

    // A render mid-drag, with a fresh callback, exactly as the layer produces.
    const second = vi.fn();
    rerender(
      <FloatingWindow
        title="the gap compounds"
        rect={RECT}
        zIndex={30}
        onRectChange={second}
        onMinimize={vi.fn()}
        onClose={vi.fn()}
        onFocus={vi.fn()}
      >
        <p>body</p>
      </FloatingWindow>,
    );

    fireEvent.pointerMove(window, { pointerId: 1, clientX: 160, clientY: 130 });

    // The drag survived, and the move went to the current callback.
    expect(second).toHaveBeenCalledWith(
      expect.objectContaining({ x: RECT.x + 60, y: RECT.y + 30 }),
    );
  });

  it("stops moving once the pointer is released", () => {
    const props = renderWindow();

    fireEvent.pointerDown(moveHandle(), { button: 0, pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerUp(window, { pointerId: 1 });
    props.onRectChange.mockClear();
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 500, clientY: 500 });

    expect(props.onRectChange).not.toHaveBeenCalled();
  });

  it("releases the page's selection lock if it unmounts mid-drag", () => {
    const { unmount } = render(
      <FloatingWindow
        title="the gap compounds"
        rect={RECT}
        zIndex={30}
        {...handlers()}
      >
        <p>body</p>
      </FloatingWindow>,
    );

    fireEvent.pointerDown(moveHandle(), { button: 0, pointerId: 1, clientX: 0, clientY: 0 });
    expect(document.body.style.userSelect).toBe("none");

    unmount();
    expect(document.body.style.userSelect).toBe("");
    expect(document.body.style.cursor).toBe("");
  });

  it("focuses the handle it was dragged by, so the arrow keys work next", () => {
    renderWindow();

    fireEvent.pointerDown(moveHandle(), {
      button: 0,
      pointerId: 1,
      clientX: 0,
      clientY: 0,
    });

    // Dragging calls preventDefault to stop text selection, which also
    // suppresses the browser's own focus. Without focusing explicitly, the
    // arrow keys go to whatever was focused before the drag.
    expect(moveHandle()).toHaveFocus();
  });

  it("ignores a right-click on the move handle", () => {
    const props = renderWindow();

    fireEvent.pointerDown(moveHandle(), { button: 2, pointerId: 1, clientX: 0, clientY: 0 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 90, clientY: 90 });

    expect(props.onRectChange).not.toHaveBeenCalled();
  });

  it("minimizes on Escape rather than closing", () => {
    const props = renderWindow();

    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

    expect(props.onMinimize).toHaveBeenCalled();
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("raises itself when anything inside takes focus", () => {
    const props = renderWindow();

    fireEvent.focus(moveHandle());

    expect(props.onFocus).toHaveBeenCalled();
  });

  it("offers minimize and close as named controls", () => {
    const props = renderWindow();

    fireEvent.click(
      screen.getByRole("button", { name: "Minimize the the gap compounds side chat" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Close the the gap compounds side chat" }),
    );

    expect(props.onMinimize).toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalled();
  });

  it("stays mounted while hidden, so a streaming answer survives minimizing", () => {
    renderWindow({ hidden: true });

    expect(screen.getByText("window body")).toBeInTheDocument();
    expect(screen.getByRole("dialog", { hidden: true })).toHaveAttribute("hidden");
  });
});
