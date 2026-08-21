import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CardsSettings } from "@/components/decks/cards-settings";
import type { DeckSourcePreference } from "@/lib/deck-types";

const sources: DeckSourcePreference[] = [
  {
    source_kind: "book",
    source_id: "11",
    title: "Designing Data-Intensive Applications",
    document_type: "book",
    status: "ready",
    cards_enabled: true,
    automatic_cards_queued: false,
  },
  {
    source_kind: "book",
    source_id: "12",
    title: "Attention Is All You Need",
    document_type: "paper",
    status: "ready",
    cards_enabled: true,
    automatic_cards_queued: true,
  },
  {
    source_kind: "video",
    source_id: "video-1",
    title: "Backpropagation lecture",
    document_type: "video",
    status: "processing",
    cards_enabled: false,
    automatic_cards_queued: false,
  },
];

function renderSettings(onSave = vi.fn().mockResolvedValue(undefined)) {
  render(
    <CardsSettings
      preferences={{ new_cards_per_day: 10, max_reviews_per_day: 120 }}
      sources={sources}
      reviewedToday={4}
      onSave={onSave}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Cards settings" }));
  return onSave;
}

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
