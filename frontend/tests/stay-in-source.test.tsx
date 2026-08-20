import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StayInSourceToggle } from "@/components/read/stay-in-source";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { useStayInSource } from "@/hooks/use-stay-in-source";

/**
 * The lock lives in the session menu, which is where the specification puts it
 * and off the path to asking a question — so this is where it is tested.
 */
function Menu({ children }: { children: React.ReactNode }) {
  return (
    <DropdownMenu defaultOpen>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost">Session actions</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>{children}</DropdownMenuContent>
    </DropdownMenu>
  );
}

function Harness({ sessionId }: { sessionId: string | null }) {
  const [locked, setLocked] = useStayInSource(sessionId);
  return (
    <Menu>
      <StayInSourceToggle locked={locked} onChange={setLocked} noun="book" />
    </Menu>
  );
}

function lock(name: RegExp) {
  return screen.getByRole("menuitemcheckbox", { name });
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("the stay-in-source lock", () => {
  it("is off by default, because escalation is automatic", async () => {
    render(<Harness sessionId="session-1" />);

    expect(await screen.findByRole("menuitemcheckbox")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("says which state it is in rather than only showing a tick", async () => {
    render(<Harness sessionId="session-1" />);

    await userEvent.click(await screen.findByRole("menuitemcheckbox"));

    expect(lock(/Staying in this book/)).toHaveAttribute("aria-checked", "true");
  });

  it("stays open on the tick, so its consequence stays readable", async () => {
    // The sentence under the label is what says what the lock does; closing
    // the menu on the click takes it away at the moment it becomes true.
    render(<Harness sessionId="session-1" />);

    await userEvent.click(await screen.findByRole("menuitemcheckbox"));

    expect(
      screen.getByText(/refused rather than answered from elsewhere/),
    ).toBeInTheDocument();
  });

  it("is remembered for the session that set it", async () => {
    const { unmount } = render(<Harness sessionId="session-1" />);
    await userEvent.click(await screen.findByRole("menuitemcheckbox"));
    unmount();

    render(<Harness sessionId="session-1" />);

    expect(await screen.findByRole("menuitemcheckbox")).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("does not follow the reader into a different source", async () => {
    // A standing instruction about this book says nothing about the next one.
    const { unmount } = render(<Harness sessionId="session-1" />);
    await userEvent.click(await screen.findByRole("menuitemcheckbox"));
    unmount();

    render(<Harness sessionId="session-2" />);

    expect(await screen.findByRole("menuitemcheckbox")).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });

  it("names the material the reader actually has open", async () => {
    render(
      <Menu>
        <StayInSourceToggle
          locked={false}
          onChange={vi.fn()}
          noun="lecture"
        />
      </Menu>,
    );

    expect(await screen.findByText("Stay in this lecture")).toBeInTheDocument();
  });
});
