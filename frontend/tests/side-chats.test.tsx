import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TurnView } from "@/components/conversation/turn-view";
import {
  MAXIMUM_QUOTE_CHARS,
  clampQuote,
  useSideChats,
} from "@/hooks/use-side-chats";
import { readGeometry } from "@/lib/floating-window";
import type { ChatTurn, SideChatSummary, TurnResult } from "@/lib/types";

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiFetch, API_BASE: "/api" }));

function summary(id: string, overrides: Partial<SideChatSummary> = {}): SideChatSummary {
  return {
    conversation_id: id,
    parent_conversation_id: "parent",
    title: `thread ${id}`,
    book_ids: [1],
    retrieval_mode: "hybrid_rerank",
    anchors: [
      { anchor_id: `${id}-anchor`, parent_turn_index: 0, quoted_text: "the gap" },
    ],
    turn_count: 0,
    created_at: "2026-08-07T00:00:00Z",
    updated_at: "2026-08-07T00:00:00Z",
    ...overrides,
  };
}

/** Exposes the hook's state so a test can assert on it and drive it. */
function Harness({
  parentId,
  onReady,
}: {
  parentId: string | null;
  onReady: (api: ReturnType<typeof useSideChats>) => void;
}) {
  const api = useSideChats(parentId);
  onReady(api);
  return (
    <ul>
      {api.windows.map((entry) => (
        <li key={entry.sideChat.conversation_id}>
          {entry.sideChat.title}
          {entry.minimized ? " (minimized)" : ""}
        </li>
      ))}
    </ul>
  );
}

function mount(parentId: string | null = "parent") {
  let api!: ReturnType<typeof useSideChats>;
  render(
    <Harness
      parentId={parentId}
      onReady={(value) => {
        api = value;
      }}
    />,
  );
  return () => api;
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe("useSideChats", () => {
  it("lists the conversation's existing side chats without opening them", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });

    const api = mount();

    await waitFor(() => expect(api().available).toHaveLength(1));
    expect(api().windows).toHaveLength(0);
  });

  it("opens a window over the passage the reader anchored", async () => {
    apiFetch.mockResolvedValueOnce({ side_chats: [] });
    const api = mount();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));

    apiFetch.mockResolvedValueOnce(summary("new"));
    await act(async () => {
      await api().open({ parentTurnIndex: 2, quotedText: "the gap compounds" });
    });

    expect(apiFetch).toHaveBeenLastCalledWith(
      "/conversations/parent/side-chats",
      expect.objectContaining({ method: "POST" }),
    );
    const [, options] = apiFetch.mock.calls.at(-1)!;
    expect(JSON.parse((options as RequestInit).body as string)).toEqual({
      anchors: [{ parent_turn_index: 2, quoted_text: "the gap compounds" }],
    });
    expect(screen.getByText("thread new")).toBeInTheDocument();
  });

  it("anchors a whole answer without sending a request that must be rejected", async () => {
    apiFetch.mockResolvedValueOnce({ side_chats: [] });
    const api = mount();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));

    apiFetch.mockResolvedValueOnce(summary("new"));
    await act(async () => {
      await api().open({
        parentTurnIndex: 0,
        // Longer than the stored limit: real answers reach 34,000 characters.
        quotedText: "sentence ".repeat(4_000),
      });
    });

    const [, options] = apiFetch.mock.calls.at(-1)!;
    const sent = JSON.parse((options as RequestInit).body as string);
    expect(sent.anchors[0].quoted_text.length).toBeLessThanOrEqual(
      MAXIMUM_QUOTE_CHARS,
    );
    expect(sent.anchors[0].quoted_text.endsWith("…")).toBe(true);
  });

  it("reports a failure to open rather than silently doing nothing", async () => {
    apiFetch.mockResolvedValueOnce({ side_chats: [] });
    const api = mount();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));

    apiFetch.mockRejectedValueOnce(new Error("turn 9 is not part of the parent"));
    await act(async () => {
      await api().open({ parentTurnIndex: 9, quotedText: "ghost" });
    });

    expect(api().error).toBe("turn 9 is not part of the parent");
    expect(api().windows).toHaveLength(0);
  });

  it("clears a reported error when it is dismissed", async () => {
    apiFetch.mockResolvedValueOnce({ side_chats: [] });
    const api = mount();
    await waitFor(() => expect(apiFetch).toHaveBeenCalledTimes(1));
    apiFetch.mockRejectedValueOnce(new Error("nope"));
    await act(async () => {
      await api().open({ parentTurnIndex: 0, quotedText: "x" });
    });
    expect(api().error).toBe("nope");

    act(() => api().dismissError());

    expect(api().error).toBe("");
  });

  it("closing a window keeps the thread available to reopen", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(1));

    act(() => api().show(summary("a")));
    expect(api().windows).toHaveLength(1);

    act(() => api().close("a"));
    expect(api().windows).toHaveLength(0);
    expect(api().available).toHaveLength(1);
  });

  it("deleting a thread removes it everywhere and forgets its geometry", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(1));
    act(() => api().show(summary("a")));
    act(() =>
      api().setRect("a", { x: 40, y: 40, width: 320, height: 300 }),
    );
    expect(readGeometry("a")).not.toBeNull();

    apiFetch.mockResolvedValueOnce(undefined);
    await act(async () => {
      await api().remove("a");
    });

    expect(api().available).toHaveLength(0);
    expect(api().windows).toHaveLength(0);
    expect(readGeometry("a")).toBeNull();
    expect(apiFetch).toHaveBeenLastCalledWith("/conversations/a", {
      method: "DELETE",
    });
  });

  it("showing an already-open thread raises it instead of opening a second window", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a"), summary("b")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(2));

    act(() => api().show(summary("a")));
    act(() => api().show(summary("b")));
    act(() => api().show(summary("a")));

    expect(api().windows).toHaveLength(2);
    expect(api().windows.at(-1)?.sideChat.conversation_id).toBe("a");
  });

  it("minimizing and restoring a window is remembered across a remount", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const first = mount();
    await waitFor(() => expect(first().available).toHaveLength(1));
    act(() => first().show(summary("a")));
    act(() => first().setMinimized("a", true));
    expect(screen.getByText(/thread a \(minimized\)/)).toBeInTheDocument();

    // A fresh mount stands in for a page reload: the window comes back, still
    // put away, because closing and minimizing mean different things.
    const second = mount();
    await waitFor(() => expect(second().windows).toHaveLength(1));
    expect(second().windows[0]?.minimized).toBe(true);
  });

  it("restores a remembered position, clamped to today's viewport", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const first = mount();
    await waitFor(() => expect(first().available).toHaveLength(1));
    act(() => first().show(summary("a")));
    act(() =>
      first().setRect("a", { x: 99_999, y: 60, width: 340, height: 300 }),
    );

    const second = mount();
    await waitFor(() => expect(second().windows).toHaveLength(1));
    const rect = second().windows[0]!.rect;
    expect(rect.x).toBeLessThan(window.innerWidth);
    expect(rect.y).toBe(60);
  });

  it("marks a minimized window when its answer settles, and clears it on restore", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(1));
    act(() => api().show(summary("a")));
    act(() => api().setMinimized("a", true));

    act(() => api().noteSettled("a", true));
    expect(api().windows[0]?.unread).toBe(true);

    act(() => api().setMinimized("a", false));
    expect(api().windows[0]?.unread).toBe(false);
  });

  it("counts a recorded turn so the thread stops reading as unused", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(1));
    act(() => api().show(summary("a")));

    act(() => api().noteSettled("a", true));

    expect(api().available[0]?.turn_count).toBe(1);
    expect(api().windows[0]?.sideChat.turn_count).toBe(1);
  });

  it("does not count a turn the server never recorded", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(1));
    act(() => api().show(summary("a")));

    // A stopped or failed turn is not stored, so counting it would misreport
    // the thread's history.
    act(() => api().noteSettled("a", false));

    expect(api().available[0]?.turn_count).toBe(0);
  });

  it("does not mark a window the reader is looking at", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const api = mount();
    await waitFor(() => expect(api().available).toHaveLength(1));
    act(() => api().show(summary("a")));

    act(() => api().noteSettled("a", true));

    expect(api().windows[0]?.unread).toBe(false);
  });

  it("asks for nothing until a conversation exists", () => {
    mount(null);

    expect(apiFetch).not.toHaveBeenCalled();
  });

  it("drops a stored window whose thread no longer exists", async () => {
    apiFetch.mockResolvedValue({ side_chats: [summary("a")] });
    const first = mount();
    await waitFor(() => expect(first().available).toHaveLength(1));
    act(() => first().show(summary("a")));

    // The thread was deleted from another tab.
    apiFetch.mockResolvedValue({ side_chats: [] });
    const second = mount();
    await waitFor(() => expect(second().available).toHaveLength(0));
    expect(second().windows).toHaveLength(0);
  });
});

function turn(overrides: Partial<ChatTurn> = {}): ChatTurn {
  const result: TurnResult = {
    question: "What is skew?",
    answer: "Features differ [S1].",
    route: "retrieval_qa",
    history_dependency: "independent",
    standalone_query: "What is skew?",
    resolved_scope: null,
    evidence: [],
    citations: [],
    figures: [],
    outline_node_ids: [],
    outcome: "answer",
    retrieval_mode: "hybrid_rerank",
    warnings: [],
    answer_archetype: "concept_explanation",
    response_depth: "quick",
    routing_reason: "direct question",
    prompt_profile_version: "v1",
    side_context: null,
  };
  return {
    id: "t1",
    question: "What is skew?",
    answer: "Features differ [S1].",
    status: "complete",
    result,
    error: null,
    turnIndex: 3,
    ...overrides,
  };
}

describe("clampQuote", () => {
  it("leaves a normal quote exactly as it was", () => {
    expect(clampQuote("  the gap compounds  ")).toBe("the gap compounds");
  });

  it("cuts an over-long quote on a word boundary and marks the cut", () => {
    const clamped = clampQuote("sentence ".repeat(4_000));

    expect(clamped.length).toBeLessThanOrEqual(MAXIMUM_QUOTE_CHARS);
    expect(clamped.endsWith("…")).toBe(true);
    expect(clamped).not.toMatch(/ …$/);
  });

  it("still cuts text that has no word boundary to cut on", () => {
    const clamped = clampQuote("x".repeat(MAXIMUM_QUOTE_CHARS + 500));

    expect(clamped.length).toBeLessThanOrEqual(MAXIMUM_QUOTE_CHARS);
  });
});

describe("asking on the side from a turn", () => {
  it("anchors to the index the server recorded, not the position on screen", () => {
    const onAskOnTheSide = vi.fn();
    render(
      <TurnView
        turn={turn()}
        isLast
        canRetry={false}
        onRetry={() => {}}
        onAskOnTheSide={onAskOnTheSide}
      />,
    );

    screen.getByRole("button", { name: "Ask on the side" }).click();

    expect(onAskOnTheSide).toHaveBeenCalledWith(3, "Features differ [S1].");
  });

  it("is not offered for a turn the server never recorded", () => {
    render(
      <TurnView
        turn={turn({ status: "stopped", turnIndex: undefined })}
        isLast
        canRetry={false}
        onRetry={() => {}}
        onAskOnTheSide={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole("button", { name: "Ask on the side" }),
    ).not.toBeInTheDocument();
  });
});
