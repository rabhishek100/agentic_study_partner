import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";

/**
 * The keyboard journeys the accessibility contract promises but nothing
 * asserted.
 *
 * Browser testing gave an ambiguous answer here: focus appeared to land on
 * `body` after Escape, but the menu had been opened with a synthetic click and
 * closed with an injected key event, so the result could not be separated from
 * the harness. `userEvent` drives real focus and key semantics, which is the
 * clean signal.
 */
function AccountMenu() {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost">reader@example.com</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuItem>Sign out</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

describe("keyboard journeys", () => {
  it("opens a menu from the keyboard and puts focus inside it", async () => {
    const user = userEvent.setup();
    render(<AccountMenu />);

    const trigger = screen.getByRole("button", { name: "reader@example.com" });
    await user.tab();
    expect(trigger).toHaveFocus();

    await user.keyboard("{Enter}");

    const item = await screen.findByRole("menuitem", { name: "Sign out" });
    await waitFor(() => expect(item).toHaveFocus());
  });

  it("returns focus to the trigger when the menu is dismissed", async () => {
    const user = userEvent.setup();
    render(<AccountMenu />);

    const trigger = screen.getByRole("button", { name: "reader@example.com" });
    await user.tab();
    await user.keyboard("{Enter}");
    await screen.findByRole("menuitem", { name: "Sign out" });

    await user.keyboard("{Escape}");

    // Losing focus to `body` would restart the next Tab at the top of the
    // document, which on the books route is 122 stops from the composer.
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
  });
});
