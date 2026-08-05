import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useResizablePane } from "@/hooks/use-resizable-pane";

const STORAGE_KEY = "test:pane-width";

function Harness({ edge }: { edge: "left" | "right" }) {
  const { percent, containerRef, separatorProps } = useResizablePane({
    storageKey: STORAGE_KEY,
    edge,
    label: "Resize the pane",
    defaultPercent: 40,
    minPercent: 25,
    maxPercent: 65,
  });
  return (
    <div ref={containerRef}>
      <div data-testid="pane" style={{ width: `${percent}%` }} />
      <div {...separatorProps} />
    </div>
  );
}

afterEach(() => {
  window.localStorage.removeItem(STORAGE_KEY);
  document.body.style.removeProperty("cursor");
  document.body.style.removeProperty("user-select");
});

describe("useResizablePane", () => {
  it("clamps a stored width that today's limits no longer allow", async () => {
    window.localStorage.setItem(STORAGE_KEY, "90");

    render(<Harness edge="left" />);

    const separator = screen.getByRole("separator", {
      name: "Resize the pane",
    });
    await waitFor(() =>
      expect(separator).toHaveAttribute("aria-valuenow", "65"),
    );
  });

  it("grows a left-docked pane with the arrow key pointing at it", () => {
    render(<Harness edge="left" />);

    const separator = screen.getByRole("separator", {
      name: "Resize the pane",
    });
    fireEvent.keyDown(separator, { key: "ArrowRight" });

    expect(separator).toHaveAttribute("aria-valuenow", "45");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("45");
  });

  it("grows a right-docked pane with the opposite arrow key", () => {
    render(<Harness edge="right" />);

    const separator = screen.getByRole("separator", {
      name: "Resize the pane",
    });
    fireEvent.keyDown(separator, { key: "ArrowLeft" });

    expect(separator).toHaveAttribute("aria-valuenow", "45");
  });

  it("releases global drag styles when the pane unmounts mid-drag", () => {
    const { unmount } = render(<Harness edge="left" />);

    fireEvent.pointerDown(
      screen.getByRole("separator", { name: "Resize the pane" }),
    );
    expect(document.body.style.cursor).toBe("col-resize");

    unmount();
    expect(document.body.style.cursor).toBe("");
    expect(document.body.style.userSelect).toBe("");
  });
});
