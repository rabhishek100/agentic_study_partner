import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AskPane } from "@/components/video/ask-pane";
import type { VideoTurn } from "@/lib/video-types";

const noop = () => {};

function pane(props: Partial<Parameters<typeof AskPane>[0]> = {}) {
  return (
    <AskPane
      videoId="video-1"
      turns={[]}
      chapters={[]}
      conversationId="conversation-a"
      isStreaming={false}
      canAsk
      blockedReason={null}
      onAsk={noop}
      onStop={noop}
      onRetry={noop}
      onSeek={noop}
      onOpenDocument={noop}
      {...props}
    />
  );
}

afterEach(() => vi.restoreAllMocks());

describe("AskPane", () => {
  it("starts each opened conversation at its latest turn", () => {
    const scrollTo = vi.fn();
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: scrollTo,
    });

    const { rerender } = render(pane());
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });

    scrollTo.mockClear();
    rerender(pane({ conversationId: "conversation-b" }));

    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: "auto" });
  });

  it("announces a settled turn without announcing every streamed token", () => {
    const streaming: VideoTurn = {
      id: "turn-1",
      question: "What was on the board?",
      answer: "A diagram",
      status: "streaming",
      result: null,
      error: null,
    };

    const { rerender } = render(pane({ turns: [streaming] }));
    expect(screen.getByRole("status")).toHaveTextContent(
      "Generating an answer.",
    );

    rerender(pane({ turns: [{ ...streaming, status: "complete" }] }));
    expect(screen.getByRole("status")).toHaveTextContent("Answer complete.");
  });
});
