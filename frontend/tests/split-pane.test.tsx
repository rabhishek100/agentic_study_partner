import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SplitPane } from "@/components/pdf/split-pane";

const STORAGE_KEY = "asp:reading-pane-width-v2";

afterEach(() => {
  window.localStorage.removeItem(STORAGE_KEY);
  document.body.style.removeProperty("cursor");
  document.body.style.removeProperty("user-select");
});

describe("SplitPane", () => {
  it("keeps a previously oversized document pane within the readable limit", async () => {
    window.localStorage.setItem(STORAGE_KEY, "70");

    render(
      <SplitPane aside={<div>Document</div>}>
        <div>Conversation</div>
      </SplitPane>,
    );

    const separator = screen.getByRole("separator", {
      name: "Resize the document pane",
    });
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "68"));
    expect(screen.getByRole("complementary", { name: "Source document" })).toHaveStyle({
      "--pane": "68%",
    });
  });

  it("supports keyboard resizing and persists the new width", () => {
    render(
      <SplitPane aside={<div>Document</div>}>
        <div>Conversation</div>
      </SplitPane>,
    );

    const separator = screen.getByRole("separator", {
      name: "Resize the document pane",
    });
    fireEvent.keyDown(separator, { key: "ArrowRight" });

    expect(separator).toHaveAttribute("aria-valuenow", "50");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("50");
  });

  it("releases global drag styles when the pane unmounts mid-drag", () => {
    const { unmount } = render(
      <SplitPane aside={<div>Document</div>}>
        <div>Conversation</div>
      </SplitPane>,
    );

    fireEvent.pointerDown(
      screen.getByRole("separator", { name: "Resize the document pane" }),
    );
    expect(document.body.style.cursor).toBe("col-resize");
    expect(document.body.style.userSelect).toBe("none");

    unmount();
    expect(document.body.style.cursor).toBe("");
    expect(document.body.style.userSelect).toBe("");
  });

  it("keeps the workspace mounted when a document opens and closes", () => {
    const workspace = <div data-testid="workspace">Conversation</div>;
    const { rerender } = render(
      <SplitPane aside={null}>{workspace}</SplitPane>,
    );
    const original = screen.getByTestId("workspace");

    rerender(<SplitPane aside={<div>Document</div>}>{workspace}</SplitPane>);
    expect(screen.getByTestId("workspace")).toBe(original);

    rerender(<SplitPane aside={null}>{workspace}</SplitPane>);
    expect(screen.getByTestId("workspace")).toBe(original);
  });
});
