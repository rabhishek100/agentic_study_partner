import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/components/app-shell";

function shell(aside?: React.ReactNode, documentControl?: React.ReactNode) {
  return render(
    <AppShell
      rail={<div>Library rail</div>}
      nav={<nav aria-label="Library sections"><a href="/">Books</a></nav>}
      status="Ready"
      account={<button type="button">Account</button>}
      documentControl={documentControl}
      aside={aside}
    >
      <div>Conversation</div>
    </AppShell>,
  );
}

describe("AppShell", () => {
  it("owns exactly one viewport and clips document-level overflow", () => {
    const { container } = shell();

    expect(container.querySelector('[data-slot="app-shell"]')).toHaveClass(
      "h-dvh",
      "max-h-dvh",
      "overflow-hidden",
    );
  });

  it("reclaims the fixed rail space while a document is open", () => {
    const { container } = shell(<div>Document viewer</div>);

    expect(container.querySelectorAll("aside")).toHaveLength(1);
    expect(
      screen.getByRole("complementary", { name: "Source document" }),
    ).toHaveTextContent("Document viewer");
    expect(
      screen.getByRole("button", { name: "Open menu" }),
    ).toBeVisible();
  });

  it("shows the fixed library rail when no document is open", () => {
    const { container } = shell();

    expect(container.querySelector("aside")).toHaveTextContent("Library rail");
    expect(
      screen.getByRole("button", { name: "Open menu" }),
    ).toBeVisible();
  });

  it("keeps the same menu available when a workspace has no contextual rail", () => {
    render(
      <AppShell
        rail={null}
        nav={
          <nav aria-label="Library sections">
            <a href="/videos">Videos</a>
          </nav>
        }
        account={<button type="button">Account</button>}
      >
        <div>Videos workspace</div>
      </AppShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Open menu" }));
    expect(screen.getByRole("dialog", { name: "Menu" })).toHaveTextContent(
      "Videos",
    );
  });

  it("keeps a minimized document restore control in the app header", () => {
    shell(undefined, <button type="button">Restore document</button>);

    expect(
      screen.getByRole("button", { name: "Restore document" }),
    ).toBeVisible();
  });

  it("lets the study context open the library drawer", () => {
    render(
      <AppShell
        rail={<div>Library rail</div>}
        status="Ready"
        account={<button type="button">Account</button>}
        contextBar={({ openRail }) => (
          <button type="button" onClick={openRail}>Change book</button>
        )}
      >
        <div>Conversation</div>
      </AppShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Change book" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("Library rail");
  });
});
