import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { AppShell } from "@/components/app-shell";
import { HEADER_INSET } from "@/lib/floating-window";

function shell(aside?: React.ReactNode, documentControl?: React.ReactNode) {
  return render(
    <AppShell
      rail={<div>Library rail</div>}
      status="Ready"
      account={<button type="button">Account</button>}
      documentControl={documentControl}
      aside={aside}
    >
      <div>Conversation</div>
    </AppShell>,
  );
}

afterEach(() => {
  window.localStorage.removeItem("asp:rail-collapsed");
});

describe("AppShell", () => {
  it("sizes the header to the inset that floating windows are clamped against", () => {
    const { container } = shell();
    const header = container.querySelector("header");

    // These were two independent numbers that happened to agree. The
    // floating-window tests assert against the imported symbol, so they pass at
    // any value, and nothing checked the header itself — a change to one would
    // have silently put every side-chat window underneath the masthead.
    expect(header).toHaveStyle({ height: `${HEADER_INSET}px` });
  });

  it("owns exactly one viewport and clips document-level overflow", () => {
    const { container } = shell();

    expect(container.querySelector('[data-slot="app-shell"]')).toHaveClass(
      "h-dvh",
      "max-h-dvh",
      "overflow-hidden",
    );
  });

  it("keeps the library rail in place while a document is open", () => {
    const { container } = shell(<div>Document viewer</div>);

    // The rail used to be gated on `!aside`, so following a citation
    // reorganised the frame underneath the reader. Both regions now coexist.
    const asides = Array.from(container.querySelectorAll("aside"));
    expect(asides).toHaveLength(2);
    expect(asides[0]).toHaveTextContent("Library rail");
    expect(
      screen.getByRole("complementary", { name: "Source document" }),
    ).toHaveTextContent("Document viewer");
  });

  it("shows the fixed library rail when no document is open", () => {
    const { container } = shell();

    expect(container.querySelector("aside")).toHaveTextContent("Library rail");
    expect(
      screen.getByRole("button", { name: "Open the library panel" }),
    ).toHaveClass("lg:hidden");
  });

  it("lets the reader collapse the rail, and remembers the choice", async () => {
    const user = userEvent.setup();
    const { container, unmount } = shell(<div>Document viewer</div>);

    const toggle = screen.getByRole("button", { name: "Hide the library panel" });
    expect(container.querySelectorAll("aside")[0]).toHaveClass("lg:block");

    await user.click(toggle);

    expect(container.querySelectorAll("aside")[0]).toHaveClass("lg:hidden");
    expect(
      screen.getByRole("button", { name: "Show the library panel" }),
    ).toHaveAttribute("aria-pressed", "false");

    // Reclaiming the space is the reader's decision, so it has to survive them
    // leaving the page — that is the difference from the behaviour it replaced.
    unmount();
    const second = shell(<div>Document viewer</div>);
    await waitFor(() =>
      expect(second.container.querySelectorAll("aside")[0]).toHaveClass("lg:hidden"),
    );
  });

  it("hides a minimized document without unmounting it", () => {
    render(
      <AppShell
        rail={<div>Library rail</div>}
        status="Ready"
        account={<button type="button">Account</button>}
        aside={<div>Document viewer</div>}
        asideHidden
      >
        <div>Conversation</div>
      </AppShell>,
    );

    // Still mounted: unmounting threw away the rendered pages and the reader's
    // place in them, and re-fetched every page on the way back.
    const region = screen.getByRole("complementary", {
      name: "Source document",
      hidden: true,
    });
    expect(region).toHaveTextContent("Document viewer");
    expect(region).toHaveClass("hidden");
    expect(screen.getByText("Conversation")).toBeVisible();
  });

  it("keeps a minimized document restore control in the app header", () => {
    shell(undefined, <button type="button">Restore document</button>);

    expect(
      screen.getByRole("button", { name: "Restore document" }),
    ).toBeVisible();
  });
});
