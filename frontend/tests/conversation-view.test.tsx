import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConversationView } from "@/components/conversation/conversation-view";

const noop = () => {};

afterEach(() => vi.restoreAllMocks());

describe("ConversationView scrolling", () => {
  it("starts each resumed conversation at its latest turn", () => {
    const scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: scrollTo,
    });

    const { rerender } = render(
      <ConversationView
        turns={[]}
        isStreaming={false}
        hasBooks={false}
        canSend={false}
        onSend={noop}
        onStop={noop}
        onRetry={noop}
        conversationId="conversation-a"
        responseDepth="quick"
        onResponseDepthChange={noop}
        books={[]}
      />,
    );
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });

    scrollTo.mockClear();
    rerender(
      <ConversationView
        turns={[]}
        isStreaming={false}
        hasBooks={false}
        canSend={false}
        onSend={noop}
        onStop={noop}
        onRetry={noop}
        conversationId="conversation-b"
        responseDepth="quick"
        onResponseDepthChange={noop}
        books={[]}
      />,
    );

    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
  });
});

describe("ConversationView source language", () => {
  it("uses paper-specific empty-state and composer copy", () => {
    render(
      <ConversationView
        turns={[]}
        isStreaming={false}
        hasBooks={false}
        canSend={false}
        onSend={noop}
        onStop={noop}
        onRetry={noop}
        conversationId={null}
        responseDepth="quick"
        onResponseDepthChange={noop}
        books={[]}
        documentType="paper"
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Upload a paper to begin" }),
    ).toBeTruthy();
    expect(screen.getByPlaceholderText("Upload a paper first…")).toBeTruthy();
    expect(
      screen.getByText("Answers are limited to the evidence found in your papers."),
    ).toBeTruthy();
  });
});
