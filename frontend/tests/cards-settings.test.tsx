import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CardsSettings } from "@/components/decks/cards-settings";
import type { DeckSourcePreference } from "@/lib/deck-types";
import {
  BROWSER_ALERT_ENABLED_AT_KEY,
  type DeckReminderPreferences,
} from "@/lib/notification-types";

const sources: DeckSourcePreference[] = [
  {
    source_kind: "book",
    source_id: "11",
    title: "Designing Data-Intensive Applications",
    document_type: "book",
    status: "ready",
    cards_enabled: true,
    automatic_cards_queued: false,
    automatic_cards_activated: false,
    missing_automatic_set_count: 2,
    can_activate_automatic_cards: true,
  },
  {
    source_kind: "book",
    source_id: "12",
    title: "Attention Is All You Need",
    document_type: "paper",
    status: "ready",
    cards_enabled: true,
    automatic_cards_queued: true,
    automatic_cards_activated: true,
    missing_automatic_set_count: 0,
    can_activate_automatic_cards: false,
  },
  {
    source_kind: "video",
    source_id: "video-1",
    title: "Backpropagation lecture",
    document_type: "video",
    status: "processing",
    cards_enabled: false,
    automatic_cards_queued: false,
    automatic_cards_activated: false,
    missing_automatic_set_count: 1,
    can_activate_automatic_cards: false,
  },
];

function renderSettings(
  onSave = vi.fn().mockResolvedValue(undefined),
  onActivate = vi.fn().mockResolvedValue({
    source: sources[0],
    jobs_queued: 2,
  }),
  reminderPreferences: DeckReminderPreferences = {
    enabled: false,
    reminder_time: "09:00",
    timezone: "UTC",
  },
  onSaveReminder = vi.fn().mockImplementation(async (preferences) => preferences),
) {
  render(
    <CardsSettings
      preferences={{ new_cards_per_day: 10, max_reviews_per_day: 120 }}
      sources={sources}
      reviewedToday={4}
      onSave={onSave}
      onActivate={onActivate}
      reminderPreferences={reminderPreferences}
      reminderLoadError=""
      onRetryReminder={vi.fn().mockResolvedValue(undefined)}
      onSaveReminder={onSaveReminder}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Cards settings" }));
  return onSave;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("Cards source settings", () => {
  it("groups books, papers, and lectures and shows paper automation", () => {
    renderSettings();

    expect(screen.getByRole("heading", { name: "Books" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Papers" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Lectures" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Automatic Set 1 queued"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Generate Set 1 for the complete paper and include its cards in Today.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", {
        name: /Designing Data-Intensive Applications/,
      }),
    ).toBeChecked();
    expect(
      screen.getByRole("checkbox", { name: /Backpropagation lecture/ }),
    ).not.toBeChecked();
  });

  it("saves daily pace and source selections through one action", async () => {
    const onSave = renderSettings();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Designing Data-Intensive Applications/,
      }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", { name: /Attention Is All You Need/ }),
    );
    fireEvent.change(screen.getByLabelText("New cards per day"), {
      target: { value: "15" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledOnce());
    const [preferences, updatedSources] = onSave.mock.calls[0]!;
    expect(preferences).toEqual({
      new_cards_per_day: 15,
      max_reviews_per_day: 120,
    });
    expect(
      updatedSources.find(
        (source: DeckSourcePreference) => source.source_id === "11",
      )?.cards_enabled,
    ).toBe(false);
    expect(
      updatedSources.find(
        (source: DeckSourcePreference) => source.source_id === "12",
      )?.cards_enabled,
    ).toBe(false);
  });

  it("saves a daily reminder separately in the browser timezone", async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "Asia/Kolkata",
    } as Intl.ResolvedDateTimeFormatOptions);
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onSaveReminder = vi
      .fn()
      .mockImplementation(async (preferences) => preferences);
    renderSettings(
      onSave,
      undefined,
      { enabled: false, reminder_time: "09:00", timezone: "UTC" },
      onSaveReminder,
    );

    expect(screen.getByText("Timezone: Asia/Kolkata")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Remind me to review today’s cards/,
      }),
    );
    fireEvent.change(screen.getByLabelText("Reminder time"), {
      target: { value: "18:30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save reminder" }));

    await waitFor(() =>
      expect(onSaveReminder).toHaveBeenCalledWith({
        enabled: true,
        reminder_time: "18:30",
        timezone: "Asia/Kolkata",
      }),
    );
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("requests browser permission only from the explicit enable button", async () => {
    const requestPermission = vi.fn().mockResolvedValue("granted");
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission,
    });
    renderSettings();

    expect(requestPermission).not.toHaveBeenCalled();
    expect(
      screen.getByText(/work only while Mugensei is open/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Enable on this browser" }),
    );

    await waitFor(() => expect(requestPermission).toHaveBeenCalledOnce());
    expect(window.localStorage.getItem(BROWSER_ALERT_ENABLED_AT_KEY)).not.toBeNull();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Browser alerts enabled while Mugensei is open",
    );
  });

  it("announces a rejected browser permission request", async () => {
    const requestPermission = vi
      .fn()
      .mockRejectedValue(new Error("Permission prompt failed"));
    vi.stubGlobal("Notification", {
      permission: "default",
      requestPermission,
    });
    renderSettings();

    fireEvent.click(
      screen.getByRole("button", { name: "Enable on this browser" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Permission prompt failed",
    );
    expect(window.localStorage.getItem(BROWSER_ALERT_ENABLED_AT_KEY)).toBeNull();
  });

  it("lets a traveler switch a saved reminder to this device timezone", async () => {
    vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
      timeZone: "Asia/Kolkata",
    } as Intl.ResolvedDateTimeFormatOptions);
    const onSaveReminder = vi
      .fn()
      .mockImplementation(async (preferences) => preferences);
    renderSettings(
      undefined,
      undefined,
      {
        enabled: true,
        reminder_time: "09:00",
        timezone: "America/New_York",
      },
      onSaveReminder,
    );

    expect(screen.getByText("Timezone: America/New_York")).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Use this device timezone" }),
    );
    expect(screen.getByText("Timezone: Asia/Kolkata")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "Save reminder to apply this timezone",
    );
    fireEvent.click(screen.getByRole("button", { name: "Save reminder" }));
    await waitFor(() =>
      expect(onSaveReminder).toHaveBeenCalledWith({
        enabled: true,
        reminder_time: "09:00",
        timezone: "Asia/Kolkata",
      }),
    );
  });

  it("requires exact-count confirmation before activating legacy sets", async () => {
    const onActivate = vi.fn().mockResolvedValue({
      source: {
        ...sources[0],
        automatic_cards_activated: true,
        missing_automatic_set_count: 0,
        can_activate_automatic_cards: false,
        automatic_cards_queued: true,
      },
      jobs_queued: 2,
    });
    const onSave = renderSettings(undefined, onActivate);
    const checkbox = screen.getByRole("checkbox", {
      name: /Designing Data-Intensive Applications/,
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Create 2 missing sets" }),
    );
    expect(onActivate).not.toHaveBeenCalled();
    expect(checkbox).toBeChecked();
    expect(
      screen.getByText(/This queues 2 chapter sets now/),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Cancel activation" }),
    );
    expect(onActivate).not.toHaveBeenCalled();
    expect(screen.queryByText(/This queues 2 chapter sets now/)).toBeNull();

    fireEvent.click(
      screen.getByRole("button", { name: "Create 2 missing sets" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create 2 sets" }));

    await waitFor(() =>
      expect(onActivate).toHaveBeenCalledWith(sources[0], 2),
    );
    expect(onSave).not.toHaveBeenCalled();
    expect(checkbox).toBeChecked();
    expect(await screen.findByRole("status")).toHaveTextContent(
      "2 automatic sets queued",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("keeps confirmation visible and announces activation failures", async () => {
    const onActivate = vi
      .fn()
      .mockRejectedValue(new Error("Source changed; refresh settings"));
    renderSettings(undefined, onActivate);

    fireEvent.click(
      screen.getByRole("button", { name: "Create 2 missing sets" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Create 2 sets" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Source changed; refresh settings",
    );
    expect(screen.getByText(/This queues 2 chapter sets now/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("keeps the dialog and selections open when saving fails", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("Network unavailable"));
    renderSettings(onSave);
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /Designing Data-Intensive Applications/,
      }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Network unavailable",
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", {
        name: /Designing Data-Intensive Applications/,
      }),
    ).not.toBeChecked();
  });
});
