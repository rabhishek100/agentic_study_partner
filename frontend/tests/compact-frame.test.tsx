import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { SplitPane } from "@/components/pdf/split-pane";
import { SECTIONS, SectionTabBar } from "@/components/section-nav";

const signOut = vi.hoisted(() => vi.fn());
const setTheme = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/use-session", () => ({ signOut }));
vi.mock("next-themes", () => ({
  useTheme: () => ({ theme: "system", setTheme }),
}));
vi.mock("@/components/notifications/notification-center", () => ({
  NotificationCenter: () => (
    <button type="button" aria-label="Notifications">
      Notifications
    </button>
  ),
}));

function shell(props: Partial<React.ComponentProps<typeof AppShell>> = {}) {
  return render(
    <AppShell
      rail={<div>Library rail</div>}
      status="Ready"
      account={<AccountMenu email="reader@example.com" />}
      {...props}
    >
      <div>Conversation</div>
    </AppShell>,
  );
}

/**
 * The `compact` rule from the design system: below 48em the frame dissolves to
 * one task with a bottom tab bar and an anchored composer.
 *
 * jsdom has no layout, so these assert the rule as it is actually expressed —
 * which breakpoint classes each piece carries — rather than measuring pixels.
 * That is the property that broke: the switcher was `hidden sm:block` and had
 * no compact counterpart at all, so five of the six sections were unreachable
 * on a phone while every unit test still passed.
 */
describe("the compact frame", () => {
  it("offers every section in the tab bar, not just the current one", () => {
    render(<SectionTabBar active="books" />);

    const bar = screen.getByRole("navigation", { name: "Library sections" });
    for (const section of SECTIONS) {
      expect(
        within(bar).getByRole("link", { name: section.label }),
      ).toHaveAttribute("href", section.href);
    }
  });

  it("names the current section rather than colouring it alone", () => {
    render(<SectionTabBar active="decks" />);

    // "Never colour alone" — the accessibility contract calls out the compact
    // tab bar by name, so the current tab has to be announced, not just washed.
    const current = screen.getByRole("link", { name: "Cards" });
    expect(current).toHaveAttribute("aria-current", "page");
    expect(
      screen.getByRole("link", { name: "Books" }),
    ).not.toHaveAttribute("aria-current");
  });

  it("draws the switcher above compact and the tab bar below it", () => {
    shell({ section: "books" });

    const navs = screen.getAllByRole("navigation", { name: "Library sections" });
    expect(navs).toHaveLength(2);
    expect(navs[0]).toHaveClass("hidden", "md:flex");
    expect(navs[1]).toHaveClass("md:hidden");
  });

  it("leaves an immersive route to its own way out", () => {
    // Reading and watching are one task with a back link, not a section.
    shell({ nav: <a href="/">Library</a> });

    expect(
      screen.queryByRole("navigation", { name: "Library sections" }),
    ).toBeNull();
    expect(screen.getByRole("link", { name: "Library" })).toBeVisible();
  });

  it("keeps the tab bar out of the scrolling row", () => {
    // A fixed bar would sit on top of the composer anchored above it; a sibling
    // of the row is what lets the composer rest on it instead.
    const { container } = shell({ section: "books" });
    const shellRoot = container.querySelector('[data-slot="app-shell"]');
    const tabBar = screen.getAllByRole("navigation", {
      name: "Library sections",
    }).at(-1);

    expect(tabBar?.parentElement).toBe(shellRoot);
  });
});

describe("AccountMenu", () => {
  it("collapses the address to an icon until the row can afford it", () => {
    render(<AccountMenu email="reader@example.com" />);

    const trigger = screen.getByRole("button", {
      name: "Account: reader@example.com",
    });
    // The address itself is what took a `max-w-44` slice of a 375px masthead.
    expect(
      within(trigger).getByText("reader@example.com"),
    ).toHaveClass("hidden", "xl:inline");
  });

  it("carries the theme choices, which the masthead drops at compact", async () => {
    const user = userEvent.setup();
    render(<AccountMenu email="reader@example.com" />);

    await user.click(
      screen.getByRole("button", { name: "Account: reader@example.com" }),
    );

    await user.click(screen.getByRole("menuitem", { name: /Light/ }));
    expect(setTheme).toHaveBeenCalledWith("light");
  });

  it("signs out", async () => {
    const user = userEvent.setup();
    render(<AccountMenu email="reader@example.com" />);

    await user.click(
      screen.getByRole("button", { name: "Account: reader@example.com" }),
    );
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    expect(signOut).toHaveBeenCalled();
  });
});

describe("the right region at compact", () => {
  it("keeps an ambient region out of the way", () => {
    // Evidence fills the region as soon as an answer is grounded — nobody
    // opened it, so nothing could close it, and as a full-bleed overlay it
    // replaced the conversation for good.
    render(
      <SplitPane
        regions={[
          {
            key: "evidence",
            label: "Evidence for this answer",
            fixedWidth: 340,
            ambient: true,
            node: <div>Sources</div>,
          },
        ]}
        active="evidence"
      >
        <div>Conversation</div>
      </SplitPane>,
    );

    expect(
      screen.getByRole("complementary", { name: "Evidence for this answer" }),
    ).toHaveClass("max-md:hidden");
  });

  it("covers only the lower canvas for a region that asked to be a sheet", () => {
    render(
      <SplitPane
        regions={[
          {
            key: "questions",
            label: "Questions in this session",
            compactSheet: true,
            node: <div>Questions</div>,
          },
        ]}
        active="questions"
      >
        <div>Lecture</div>
      </SplitPane>,
    );

    expect(
      screen.getByRole("complementary", { name: "Questions in this session" }),
    ).toHaveClass("max-md:top-1/3");
  });

  it("still covers the whole canvas for a region that did not", () => {
    render(
      <SplitPane
        regions={[
          { key: "document", label: "Source document", node: <div>Page</div> },
        ]}
        active="document"
      >
        <div>Conversation</div>
      </SplitPane>,
    );

    const region = screen.getByRole("complementary", { name: "Source document" });
    expect(region).toHaveClass("inset-0");
    expect(region).not.toHaveClass("max-md:top-1/3");
    expect(region).not.toHaveClass("max-md:hidden");
  });
});
