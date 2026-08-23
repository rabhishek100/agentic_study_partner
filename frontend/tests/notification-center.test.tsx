import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationCenter } from "@/components/notifications/notification-center";
import { apiFetch } from "@/lib/api";
import type {
  NotificationListResponse,
  StudyNotification,
} from "@/lib/notification-types";
import { BROWSER_ALERT_ENABLED_AT_KEY } from "@/lib/notification-types";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

function notice(
  id: string,
  overrides: Partial<StudyNotification> = {},
): StudyNotification {
  return {
    notification_id: id,
    kind: "daily_cards_review",
    title: "Cards ready for review",
    body: "It's time for your daily review. Open Today to see what's ready now.",
    href: "/decks?review=today",
    payload: { review_count: 12 },
    created_at: "2026-08-21T03:30:00Z",
    read_at: null,
    ...overrides,
  };
}

function response(items: StudyNotification[]): NotificationListResponse {
  return {
    notifications: items,
    unread_count: items.filter((item) => !item.read_at).length,
  };
}

beforeEach(() => {
  push.mockReset();
  vi.mocked(apiFetch).mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("persistent notification center", () => {
  it("shows unread history and persists read, dismiss, and read-all actions", async () => {
    const first = notice("one");
    const second = notice("two", {
      title: "Automatic cards ready",
      body: "Set 1 is ready for Chapter 2.",
      href: "/decks/deck-2",
    });
    let items = [first, second];
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/notifications") return response(items);
      if (path === "/notifications/one/read") {
        items = items.map((item) =>
          item.notification_id === "one"
            ? { ...item, read_at: "2026-08-21T04:00:00Z" }
            : item,
        );
      }
      if (path === "/notifications/read-all") {
        items = items.map((item) => ({
          ...item,
          read_at: item.read_at ?? "2026-08-21T04:00:00Z",
        }));
      }
      if (path === "/notifications/two/dismiss") {
        items = items.filter((item) => item.notification_id !== "two");
      }
      return {};
    });
    const user = userEvent.setup();
    render(<NotificationCenter />);

    const trigger = await screen.findByRole("button", {
      name: "Notifications, 2 unread",
    });
    await user.click(trigger);
    expect(
      screen.getByRole("heading", { name: "Notifications" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "It's time for your daily review. Open Today to see what's ready now.",
      ),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: "Unread notification: Cards ready for review",
      }),
    );
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/notifications/one/read", {
        method: "POST",
      }),
    );
    expect(push).toHaveBeenCalledWith("/decks?review=today");
    expect(
      screen.getByRole("button", { name: "Notifications, 1 unread" }),
    ).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "Notifications, 1 unread" }),
    );
    await user.click(screen.getByRole("button", { name: "Mark all read" }));
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/notifications/read-all", {
        method: "POST",
      }),
    );
    await user.click(
      screen.getByRole("button", {
        name: "Dismiss notification: Automatic cards ready",
      }),
    );
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/notifications/two/dismiss", {
        method: "POST",
      }),
    );
    expect(screen.queryByText("Set 1 is ready for Chapter 2.")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Notifications" }),
    ).toBeInTheDocument();
  });

  it("emits each unread browser alert once and marks it read when clicked", async () => {
    const alerts: FakeNotification[] = [];
    class FakeNotification {
      static permission: NotificationPermission = "granted";
      onclick: (() => void) | null = null;
      close = vi.fn();
      constructor(
        readonly title: string,
        readonly options?: NotificationOptions,
      ) {
        alerts.push(this);
      }
    }
    vi.stubGlobal("Notification", FakeNotification);
    window.localStorage.setItem(
      BROWSER_ALERT_ENABLED_AT_KEY,
      "2026-08-21T00:00:00Z",
    );
    vi.spyOn(window, "focus").mockImplementation(() => undefined);
    window.localStorage.setItem(
      "mugensei:browser-notifications-delivered",
      JSON.stringify(["already-delivered"]),
    );
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/notifications") {
        return response([
          notice("new"),
          notice("already-delivered"),
          notice("already-read", { read_at: "2026-08-21T04:00:00Z" }),
        ]);
      }
      return {};
    });

    const view = render(<NotificationCenter />);
    await waitFor(() => expect(alerts).toHaveLength(1));
    expect(alerts[0]?.title).toBe("Cards ready for review");
    expect(alerts[0]?.options?.tag).toBe("mugensei:new");

    act(() => alerts[0]?.onclick?.());
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/notifications/new/read", {
        method: "POST",
      }),
    );
    expect(push).toHaveBeenCalledWith("/decks?review=today");
    expect(alerts[0]?.close).toHaveBeenCalledOnce();

    view.unmount();
    render(<NotificationCenter />);
    await waitFor(() => expect(apiFetch).toHaveBeenCalledWith("/notifications"));
    expect(alerts).toHaveLength(1);
  });

  it("rejects external notification destinations", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/notifications") {
        return response([notice("external", { href: "https://example.com" })]);
      }
      return {};
    });
    render(<NotificationCenter />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Notifications, 1 unread" }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Unread notification: Cards ready for review",
      }),
    );
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/notifications/external/read", {
        method: "POST",
      }),
    );
    expect(push).not.toHaveBeenCalled();
  });

  it("opens legacy daily reminders in today's review", async () => {
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/notifications") {
        return response([notice("legacy", { href: "/decks" })]);
      }
      return {};
    });
    render(<NotificationCenter />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Notifications, 1 unread" }),
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "Unread notification: Cards ready for review",
      }),
    );

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/decks?review=today"),
    );
  });

  it("keeps in-app history available when native alerts fail", async () => {
    class BrokenNotification {
      static permission: NotificationPermission = "granted";
      constructor() {
        throw new Error("Native notification unavailable");
      }
    }
    vi.stubGlobal("Notification", BrokenNotification);
    window.localStorage.setItem(
      BROWSER_ALERT_ENABLED_AT_KEY,
      "2026-08-21T00:00:00Z",
    );
    vi.mocked(apiFetch).mockResolvedValue(response([notice("still-visible")]));
    render(<NotificationCenter />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Notifications, 1 unread" }),
    );
    expect(
      screen.getByText(
        "It's time for your daily review. Open Today to see what's ready now.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();

    const callsBeforeFocus = vi.mocked(apiFetch).mock.calls.length;
    fireEvent.focus(window);
    await waitFor(() =>
      expect(vi.mocked(apiFetch).mock.calls.length).toBeGreaterThan(
        callsBeforeFocus,
      ),
    );
  });

  it("baselines granted permission instead of dumping unread backlog", async () => {
    const alerts: string[] = [];
    class FakeNotification {
      static permission: NotificationPermission = "granted";
      constructor(title: string) {
        alerts.push(title);
      }
    }
    vi.stubGlobal("Notification", FakeNotification);
    vi.mocked(apiFetch).mockResolvedValue(response([notice("old-unread")]));
    render(<NotificationCenter />);

    await screen.findByRole("button", { name: "Notifications, 1 unread" });
    expect(alerts).toEqual([]);
    expect(window.localStorage.getItem(BROWSER_ALERT_ENABLED_AT_KEY)).not.toBeNull();
  });

  it("ignores an overlapping stale load after dismiss", async () => {
    const item = notice("race");
    let getCount = 0;
    let resolveStale: ((value: NotificationListResponse) => void) | undefined;
    const stale = new Promise<NotificationListResponse>((resolve) => {
      resolveStale = resolve;
    });
    vi.mocked(apiFetch).mockImplementation(async (path) => {
      if (path === "/notifications") {
        getCount += 1;
        return getCount === 1 ? response([item]) : stale;
      }
      return {};
    });
    render(<NotificationCenter />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Notifications, 1 unread" }),
    );
    await waitFor(() => expect(getCount).toBeGreaterThanOrEqual(2));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Dismiss notification: Cards ready for review",
      }),
    );
    await waitFor(() =>
      expect(apiFetch).toHaveBeenCalledWith("/notifications/race/dismiss", {
        method: "POST",
      }),
    );
    expect(
      screen.queryByText(
        "It's time for your daily review. Open Today to see what's ready now.",
      ),
    ).toBeNull();

    act(() => resolveStale?.(response([item])));
    await waitFor(() =>
      expect(
        screen.queryByText(
          "It's time for your daily review. Open Today to see what's ready now.",
        ),
      ).toBeNull(),
    );
  });
});
