import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppShell } from "@/components/app-shell";

function shell(aside?: React.ReactNode) {
  return render(
    <AppShell
      rail={<div>Library rail</div>}
      status="Ready"
      account={<button type="button">Account</button>}
      aside={aside}
    >
      <div>Conversation</div>
    </AppShell>,
  );
}

describe("AppShell", () => {
  it("reclaims the fixed rail space while a document is open", () => {
    const { container } = shell(<div>Document viewer</div>);

    expect(container.querySelectorAll("aside")).toHaveLength(1);
    expect(
      screen.getByRole("complementary", { name: "Source document" }),
    ).toHaveTextContent("Document viewer");
    expect(
      screen.getByRole("button", { name: "Open the library panel" }),
    ).not.toHaveClass("lg:hidden");
  });

  it("shows the fixed library rail when no document is open", () => {
    const { container } = shell();

    expect(container.querySelector("aside")).toHaveTextContent("Library rail");
    expect(
      screen.getByRole("button", { name: "Open the library panel" }),
    ).toHaveClass("lg:hidden");
  });
});
