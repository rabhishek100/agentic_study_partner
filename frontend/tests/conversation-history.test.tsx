import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  ConversationHistory,
  groupByRecency,
  recencyGroup,
} from "@/components/conversation-history";
import type { ConversationSummary } from "@/lib/types";

const NOW = new Date("2026-07-28T10:00:00Z");

function conversation(
  overrides: Partial<ConversationSummary> = {},
): ConversationSummary {
  return {
    conversation_id: "c1",
    title: "Random forests",
    book_ids: [1],
    retrieval_mode: "hybrid",
    turn_count: 2,
    created_at: "2026-07-28T09:00:00Z",
    updated_at: "2026-07-28T09:30:00Z",
    ...overrides,
  };
}

describe("recencyGroup", () => {
  it("buckets by day, week, and month", () => {
    expect(recencyGroup("2026-07-28T09:00:00Z", NOW)).toBe("Today");
    expect(recencyGroup("2026-07-27T09:00:00Z", NOW)).toBe("Yesterday");
    expect(recencyGroup("2026-07-24T09:00:00Z", NOW)).toBe("Previous 7 days");
    expect(recencyGroup("2026-07-10T09:00:00Z", NOW)).toBe("Previous 30 days");
    expect(recencyGroup("2026-01-10T09:00:00Z", NOW)).toBe("Older");
  });
});

describe("groupByRecency", () => {
  it("keeps the server's newest-first order across groups", () => {
    const groups = groupByRecency(
      [
        conversation({ conversation_id: "a", updated_at: "2026-07-28T09:00:00Z" }),
        conversation({ conversation_id: "b", updated_at: "2026-07-27T09:00:00Z" }),
        conversation({ conversation_id: "c", updated_at: "2026-07-20T09:00:00Z" }),
      ],
      NOW,
    );

    expect(groups.map((group) => group.label)).toEqual([
      "Today",
      "Yesterday",
      "Previous 30 days",
    ]);
  });

  it("collects several conversations into one group", () => {
    const groups = groupByRecency(
      [
        conversation({ conversation_id: "a", updated_at: "2026-07-28T09:00:00Z" }),
        conversation({ conversation_id: "b", updated_at: "2026-07-28T08:00:00Z" }),
      ],
      NOW,
    );

    expect(groups).toHaveLength(1);
    expect(groups[0]?.items).toHaveLength(2);
  });
});

function renderHistory(
  overrides: Partial<React.ComponentProps<typeof ConversationHistory>> = {},
) {
  const props = {
    conversations: [conversation()],
    loaded: true,
    activeId: null,
    onOpen: vi.fn(),
    onRename: vi.fn(),
    onDelete: vi.fn(),
    onNew: vi.fn(),
    now: NOW,
    ...overrides,
  };
  render(<ConversationHistory {...props} />);
  return props;
}

describe("ConversationHistory", () => {
  it("opens a conversation when its row is activated", async () => {
    const user = userEvent.setup();
    const { onOpen } = renderHistory();

    await user.click(screen.getByRole("button", { name: /^Random forests/ }));

    expect(onOpen).toHaveBeenCalledExactlyOnceWith("c1");
  });

  it("marks the open conversation for assistive technology", () => {
    renderHistory({ activeId: "c1" });
    expect(
      screen.getByRole("button", { name: /^Random forests/ }),
    ).toHaveAttribute("aria-current", "true");
  });

  it("requires confirmation before deleting", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderHistory();

    await user.click(screen.getByLabelText("Actions for Random forests"));
    await user.click(screen.getByRole("menuitem", { name: /Delete/ }));
    expect(onDelete).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Delete" }));
    expect(onDelete).toHaveBeenCalledExactlyOnceWith("c1");
  });

  it("abandons a delete when cancelled", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderHistory();

    await user.click(screen.getByLabelText("Actions for Random forests"));
    await user.click(screen.getByRole("menuitem", { name: /Delete/ }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /^Random forests/ })).toBeVisible();
  });

  it("renames on Enter and discards on Escape", async () => {
    const user = userEvent.setup();
    const { onRename } = renderHistory();

    await user.click(screen.getByLabelText("Actions for Random forests"));
    await user.click(screen.getByRole("menuitem", { name: /Rename/ }));

    const field = screen.getByLabelText("Conversation title");
    await user.clear(field);
    await user.type(field, "Tree ensembles{Enter}");

    expect(onRename).toHaveBeenCalledExactlyOnceWith("c1", "Tree ensembles");
  });

  it("refuses to save an empty title", async () => {
    const user = userEvent.setup();
    const { onRename } = renderHistory();

    await user.click(screen.getByLabelText("Actions for Random forests"));
    await user.click(screen.getByRole("menuitem", { name: /Rename/ }));

    const field = screen.getByLabelText("Conversation title");
    await user.clear(field);
    await user.type(field, "{Enter}");

    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Save title")).toBeDisabled();
  });

  it("explains an empty history rather than showing nothing", () => {
    renderHistory({ conversations: [] });
    expect(screen.getByText(/Nothing yet/)).toBeInTheDocument();
  });

  it("shows a skeleton before the list arrives", () => {
    renderHistory({ conversations: [], loaded: false });
    expect(screen.queryByText(/Nothing yet/)).toBeNull();
  });
});
