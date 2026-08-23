import { render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TodayReviewIntent } from "@/components/decks/today-review-intent";

const { useSearchParams } = vi.hoisted(() => ({
  useSearchParams: vi.fn(),
}));

vi.mock("next/navigation", () => ({ useSearchParams }));

beforeEach(() => useSearchParams.mockReset());

describe("Today review navigation intent", () => {
  it("starts today's review from its notification deep link", async () => {
    useSearchParams.mockReturnValue(new URLSearchParams("review=today"));
    const onRequested = vi.fn();

    render(<TodayReviewIntent onRequested={onRequested} />);

    await waitFor(() => expect(onRequested).toHaveBeenCalledOnce());
  });

  it("does not start review on the ordinary deck library route", () => {
    useSearchParams.mockReturnValue(new URLSearchParams());
    const onRequested = vi.fn();

    render(<TodayReviewIntent onRequested={onRequested} />);

    expect(onRequested).not.toHaveBeenCalled();
  });
});
