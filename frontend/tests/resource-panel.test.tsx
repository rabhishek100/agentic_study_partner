import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ResourcePanel } from "@/components/video/side-panels";
import type { VideoResource } from "@/lib/video-types";

function resource(overrides: Partial<VideoResource> = {}): VideoResource {
  return {
    resource_id: "r-1",
    resource_kind: "pdf",
    origin: "upload",
    status: "failed",
    title: "fall25-cme295-lecture1",
    source_url: null,
    page_count: null,
    role: "slides",
    required: false,
    ...overrides,
  };
}

describe("ResourcePanel", () => {
  it("offers a way to remove a document that could not be read", async () => {
    const onDetach = vi.fn();
    render(
      <ResourcePanel
        resources={[resource()]}
        onOpen={() => {}}
        onDetach={onDetach}
      />,
    );

    expect(screen.getByText(/Could not be read/i)).toBeTruthy();
    await userEvent.click(
      screen.getByRole("button", { name: /Remove fall25-cme295-lecture1/i }),
    );
    expect(onDetach).toHaveBeenCalledTimes(1);
  });

  it("shows the attach control even when nothing is attached yet", () => {
    render(
      <ResourcePanel
        resources={[]}
        onOpen={() => {}}
        attach={<div>attach here</div>}
      />,
    );

    // Without this the panel was read-only: a lecture could never gain slides
    // after it was created.
    expect(screen.getByText("attach here")).toBeTruthy();
  });

  it("prompts a rebuild only while a document is still unread", () => {
    const { rerender } = render(
      <ResourcePanel
        resources={[resource({ status: "pending" })]}
        onOpen={() => {}}
        onRebuild={() => {}}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Rebuild with these documents/i }),
    ).toBeTruthy();

    rerender(
      <ResourcePanel
        resources={[resource({ status: "ready", page_count: 135 })]}
        onOpen={() => {}}
        onRebuild={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: /Rebuild/i })).toBeNull();
    expect(screen.getByText(/135 pages/)).toBeTruthy();
  });
});
