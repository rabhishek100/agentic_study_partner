import { render } from "@testing-library/react";
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
