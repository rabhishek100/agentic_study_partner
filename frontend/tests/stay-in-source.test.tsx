import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StayInSourceToggle } from "@/components/read/stay-in-source";
import { useStayInSource } from "@/hooks/use-stay-in-source";

function Harness({ sessionId }: { sessionId: string | null }) {
  const [locked, setLocked] = useStayInSource(sessionId);
  return (
    <StayInSourceToggle locked={locked} onChange={setLocked} noun="book" />
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("the stay-in-source lock", () => {
  it("is off by default, because escalation is automatic", async () => {
    render(<Harness sessionId="session-1" />);

    const toggle = screen.getByRole("button", { name: "Stay in this book" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
  });

  it("says which state it is in rather than only showing an icon", async () => {
    render(<Harness sessionId="session-1" />);

    await userEvent.click(screen.getByRole("button", { name: "Stay in this book" }));

    expect(
      screen.getByRole("button", { name: "Staying in this book" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("is remembered for the session that set it", async () => {
    const { unmount } = render(<Harness sessionId="session-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Stay in this book" }));
    unmount();

    render(<Harness sessionId="session-1" />);

    expect(
      await screen.findByRole("button", { name: "Staying in this book" }),
    ).toBeInTheDocument();
  });

  it("does not follow the reader into a different source", async () => {
    // A standing instruction about this book says nothing about the next one.
    const { unmount } = render(<Harness sessionId="session-1" />);
    await userEvent.click(screen.getByRole("button", { name: "Stay in this book" }));
    unmount();

    render(<Harness sessionId="session-2" />);

    expect(
      screen.getByRole("button", { name: "Stay in this book" }),
    ).toHaveAttribute("aria-pressed", "false");
  });

  it("names the material the reader actually has open", () => {
    render(
      <StayInSourceToggle locked={false} onChange={vi.fn()} noun="lecture" />,
    );

    expect(
      screen.getByRole("button", { name: "Stay in this lecture" }),
    ).toBeInTheDocument();
  });
});
