import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Answer } from "@/components/conversation/answer";
import { SideChatComposer } from "@/components/side-chat/side-chat-composer";
import { TooltipProvider } from "@/components/ui/tooltip";
import { VideoAnswer } from "@/components/video/video-answer";
import { describeFailure } from "@/hooks/use-side-chat";

import { BOOK_SIDE_CHATS, VIDEO_SIDE_CHATS } from "@/lib/side-chat";

const apiFetch = vi.hoisted(() => vi.fn(async () => ({ conversation_id: "side-1", turns: [] })));
vi.mock("@/lib/api", () => ({ apiFetch, API_BASE: "/api" }));
vi.mock("@/lib/supabase", () => ({ accessToken: async () => "token" }));

describe("describeFailure", () => {
  it("reads a plain string detail", () => {
    expect(describeFailure(JSON.stringify({ detail: "side chat not found" }))).toBe(
      "side chat not found",
    );
  });

  it("names the field and reason in a validation detail", () => {
    // FastAPI reports a rejected request as a list of field errors. Reading it
    // as a string put "[object Object]" in the window and hid the cause.
    const body = JSON.stringify({
      detail: [
        {
          type: "extra_forbidden",
          loc: ["body", "response_depth"],
          msg: "Extra inputs are not permitted",
        },
      ],
    });

    expect(describeFailure(body)).toBe(
      "response_depth: Extra inputs are not permitted",
    );
  });

  it("joins several field errors", () => {
    const body = JSON.stringify({
      detail: [
        { loc: ["body", "question"], msg: "Field required" },
        { loc: ["body", "extra"], msg: "Extra inputs are not permitted" },
      ],
    });

    expect(describeFailure(body)).toBe(
      "question: Field required; extra: Extra inputs are not permitted",
    );
  });

  it("never returns an object to be stringified by accident", () => {
    const described = describeFailure(JSON.stringify({ detail: { code: 7 } }));

    expect(described).not.toContain("[object Object]");
    expect(described).toBe('{"code":7}');
  });

  it("returns null for a body it cannot read", () => {
    expect(describeFailure("")).toBeNull();
    expect(describeFailure("<html>502</html>")).toBeNull();
    expect(describeFailure(JSON.stringify({}))).toBeNull();
  });
});

describe("surface descriptors", () => {
  it("only the book chat accepts an answer depth", () => {
    // The lecture turn contract forbids unknown fields, so sending a depth
    // there failed every turn with a validation error.
    expect(BOOK_SIDE_CHATS.supportsDepth).toBe(true);
    expect(VIDEO_SIDE_CHATS.supportsDepth).toBe(false);
  });

  it("addresses each surface's own endpoints", () => {
    expect(BOOK_SIDE_CHATS.stream("s1")).toBe("/side-chats/s1/turns/stream");
    expect(VIDEO_SIDE_CHATS.stream("s1")).toBe(
      "/video-side-chats/s1/turns/stream",
    );
  });
});

describe("the body a side turn sends", () => {
  /**
   * The bug this pins: the hook always sent `response_depth`, the lecture turn
   * contract forbids unknown fields, and every lecture side turn came back a
   * validation error.
   */
  async function sentBody(surface: typeof BOOK_SIDE_CHATS) {
    const { useSideChat } = await import("@/hooks/use-side-chat");
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        ({
          ok: true,
          body: {
            getReader: () => ({
              read: async () => ({ done: true, value: undefined }),
            }),
          },
        }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchMock);

    let hook: ReturnType<typeof useSideChat<unknown>> | undefined;
    function Harness() {
      hook = useSideChat<unknown>("side-1", surface);
      return null;
    }
    render(<Harness />);
    await act(async () => {
      await hook!.send("why does that matter?", "quick");
    });

    const streamCall = fetchMock.mock.calls.find((call) =>
      String(call[0]).includes("turns/stream"),
    );
    return JSON.parse(String(streamCall?.[1]?.body));
  }

  it("sends a depth and the lock for the book chat, which honours both", async () => {
    expect(await sentBody(BOOK_SIDE_CHATS)).toEqual({
      question: "why does that matter?",
      response_depth: "quick",
      // Sent on every turn rather than stored on the session: the lock is an
      // instruction about the question being asked, and the server never has
      // to guess what the current one is.
      stay_in_source: false,
    });
  });

  it("sends only the question for the lecture chat, which forbids extras", async () => {
    // Including the lock: that surface has no route out of the recording, so
    // there is nothing for one to stop.
    expect(await sentBody(VIDEO_SIDE_CHATS)).toEqual({
      question: "why does that matter?",
    });
  });
});

describe("SideChatComposer", () => {
  function renderComposer(showDepth: boolean) {
    const onSubmit = vi.fn();
    render(
      <SideChatComposer
        isStreaming={false}
        responseDepth="quick"
        onResponseDepthChange={vi.fn()}
        onSubmit={onSubmit}
        onStop={vi.fn()}
        showDepth={showDepth}
        label="the passage"
      />,
    );
    return onSubmit;
  }

  it("offers a depth control where depth is honoured", () => {
    renderComposer(true);

    expect(
      screen.getByRole("combobox", { name: /Answer depth/ }),
    ).toBeInTheDocument();
  });

  it("hides it where it would be ignored", () => {
    renderComposer(false);

    expect(
      screen.queryByRole("combobox", { name: /Answer depth/ }),
    ).not.toBeInTheDocument();
    // The send button stays reachable either way.
    expect(
      screen.getByRole("button", { name: "Send this question" }),
    ).toBeInTheDocument();
  });
});

describe("both answer bodies are quotable", () => {
  it("marks the book answer as a passage", () => {
    const { container } = render(
      <TooltipProvider>
        <Answer text="Features differ." evidence={[]} citations={[]} figures={[]} />
      </TooltipProvider>,
    );

    expect(container.querySelector("[data-answer]")).not.toBeNull();
  });

  it("marks the lecture answer as a passage", () => {
    // Missing here, the selection popover refused every lecture highlight, so
    // "Ask about this" never appeared on a video answer.
    const { container } = render(
      <TooltipProvider>
        <VideoAnswer
          videoId="video-1"
          answer="The lecturer explains attention."
          evidence={[]}
          citations={[]}
          onSeek={vi.fn()}
          onOpenDocument={vi.fn()}
        />
      </TooltipProvider>,
    );

    expect(container.querySelector("[data-answer]")).not.toBeNull();
  });
});
