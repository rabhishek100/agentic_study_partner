import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SplitPane } from "@/components/pdf/split-pane";

const STORAGE_KEY = "asp:reading-pane-width";

afterEach(() => {
  window.localStorage.removeItem(STORAGE_KEY);
  document.body.style.removeProperty("cursor");
  document.body.style.removeProperty("user-select");
});

describe("SplitPane", () => {
  it("keeps a previously oversized document pane within the readable limit", async () => {
    window.localStorage.setItem(STORAGE_KEY, "70");

    render(
      <SplitPane
        regions={[{ key: "document", label: "Source document", node: <div>Document</div> }]}
        active="document"
      >
        <div>Conversation</div>
      </SplitPane>,
    );

    const separator = screen.getByRole("separator", {
      name: "Resize the document pane",
    });
    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "62"));
    expect(screen.getByRole("complementary", { name: "Source document" })).toHaveStyle({
      "--pane": "62%",
    });
  });

  it("supports keyboard resizing and persists the new width", () => {
    render(
      <SplitPane
        regions={[{ key: "document", label: "Source document", node: <div>Document</div> }]}
        active="document"
      >
        <div>Conversation</div>
      </SplitPane>,
    );

    const separator = screen.getByRole("separator", {
      name: "Resize the document pane",
    });
    fireEvent.keyDown(separator, { key: "ArrowRight" });

    expect(separator).toHaveAttribute("aria-valuenow", "45");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("45");
  });

  it("releases global drag styles when the pane unmounts mid-drag", () => {
    const { unmount } = render(
      <SplitPane
        regions={[{ key: "document", label: "Source document", node: <div>Document</div> }]}
        active="document"
      >
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
      <SplitPane regions={[]} active={null}>{workspace}</SplitPane>,
    );
    const original = screen.getByTestId("workspace");

    rerender(<SplitPane
        regions={[{ key: "document", label: "Source document", node: <div>Document</div> }]}
        active="document"
      >{workspace}</SplitPane>);
    expect(screen.getByTestId("workspace")).toBe(original);

    rerender(<SplitPane regions={[]} active={null}>{workspace}</SplitPane>);
    expect(screen.getByTestId("workspace")).toBe(original);
  });

  it("gives a mode its own width, its own range and its own memory", async () => {
    // A chat column beside a page wants a quarter of the row where a document
    // beside a conversation wants half. One shared key and range gives
    // whichever mode opened second the other one's width.
    window.localStorage.setItem("asp:questions-width", "24");
    render(
      <SplitPane
        active="questions"
        regions={[
          {
            key: "questions",
            label: "Questions",
            node: <p>questions</p>,
            resize: {
              storageKey: "asp:questions-width",
              defaultPercent: 28,
              minPercent: 18,
              maxPercent: 55,
              label: "Resize the questions pane",
            },
          },
        ]}
      >
        <p>document</p>
      </SplitPane>,
    );

    const separator = await screen.findByRole("separator", {
      name: "Resize the questions pane",
    });
    expect(separator).toHaveAttribute("aria-valuemin", "18");
    expect(separator).toHaveAttribute("aria-valuemax", "55");
    expect(separator).toHaveAttribute("aria-valuenow", "24");
  });
});
