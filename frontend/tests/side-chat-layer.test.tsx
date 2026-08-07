import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import type { SideChatWindow as SideChatWindowState } from "@/hooks/use-side-chats";
import { DEFAULT_HEIGHT, DEFAULT_WIDTH } from "@/lib/floating-window";

// The window's own body loads its turns over the network. This suite is about
// the layer: stacking, the dock, and the narrow-viewport fallback.
vi.mock("@/components/side-chat/side-chat-window", () => ({
  SideChatWindow: ({
    window: state,
    zIndex,
    docked,
  }: {
    window: SideChatWindowState;
    zIndex: number;
    docked?: boolean;
  }) => (
    <div
      data-testid={`window-${state.sideChat.conversation_id}`}
      data-z={zIndex}
      data-docked={docked ? "yes" : "no"}
      data-minimized={state.minimized ? "yes" : "no"}
    >
      {state.sideChat.title}
    </div>
  ),
}));

function windowState(
  id: string,
  overrides: Partial<SideChatWindowState> = {},
): SideChatWindowState {
  return {
    sideChat: {
      conversation_id: id,
      parent_conversation_id: "parent",
      title: `thread ${id}`,
      book_ids: [1],
      retrieval_mode: "hybrid_rerank",
      anchors: [],
      turn_count: 0,
      created_at: "2026-08-07T00:00:00Z",
      updated_at: "2026-08-07T00:00:00Z",
    },
    rect: { x: 10, y: 10, width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT },
    minimized: false,
    unread: false,
    ...overrides,
  };
}

function setViewport(floating: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: floating,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

const handlers = () => ({
  onRectChange: vi.fn(),
  onMinimize: vi.fn(),
  onClose: vi.fn(),
  onFocus: vi.fn(),
  onSettled: vi.fn(),
  onAnchorsChange: vi.fn(),
  resolveQuoteTurn: vi.fn(() => 0),
});

describe("SideChatLayer on a wide viewport", () => {
  beforeEach(() => setViewport(true));

  it("renders nothing when no window is open", () => {
    const { container } = render(
      <SideChatLayer windows={[]} {...handlers()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("reports a side chat that could not be opened, when there is no window", () => {
    // The case that made a click look like it did nothing at all.
    const onDismissError = vi.fn();
    render(
      <SideChatLayer
        windows={[]}
        {...handlers()}
        error="turn 9 is not part of the parent conversation"
        onDismissError={onDismissError}
      />,
    );

    expect(
      screen.getByText("turn 9 is not part of the parent conversation"),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Dismiss this message" }),
    );
    expect(onDismissError).toHaveBeenCalled();
  });

  it("reports an error alongside the windows that are open", () => {
    render(
      <SideChatLayer
        windows={[windowState("a")]}
        {...handlers()}
        error="Could not reach the study API."
      />,
    );

    expect(
      screen.getByText("Could not reach the study API."),
    ).toBeInTheDocument();
    expect(screen.getByTestId("window-a")).toBeInTheDocument();
  });

  it("stacks later windows above earlier ones", () => {
    render(
      <SideChatLayer
        windows={[windowState("a"), windowState("b"), windowState("c")]}
        {...handlers()}
      />,
    );

    const z = ["a", "b", "c"].map((id) =>
      Number(screen.getByTestId(`window-${id}`).dataset.z),
    );
    expect(z[0]).toBeLessThan(z[1]!);
    expect(z[1]).toBeLessThan(z[2]!);
  });

  it("keeps a minimized window mounted so its answer keeps arriving", () => {
    render(
      <SideChatLayer
        windows={[windowState("a", { minimized: true })]}
        {...handlers()}
      />,
    );

    expect(screen.getByTestId("window-a")).toHaveAttribute(
      "data-minimized",
      "yes",
    );
  });

  it("docks a minimized window as a restorable chat head", () => {
    const props = handlers();
    render(
      <SideChatLayer
        windows={[windowState("a", { minimized: true })]}
        {...props}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /thread a/ }));

    expect(props.onMinimize).toHaveBeenCalledWith("a", false);
  });

  it("marks a docked window whose answer landed while it was away", () => {
    render(
      <SideChatLayer
        windows={[windowState("a", { minimized: true, unread: true })]}
        {...handlers()}
      />,
    );

    expect(screen.getByLabelText("has a new answer")).toBeInTheDocument();
  });

  it("does not dock a window that is on screen", () => {
    render(<SideChatLayer windows={[windowState("a")]} {...handlers()} />);

    expect(
      screen.queryByRole("button", { name: /thread a/ }),
    ).not.toBeInTheDocument();
  });
});

describe("SideChatLayer on a narrow viewport", () => {
  beforeEach(() => setViewport(false));

  it("replaces the floating windows with a tabbed sheet", () => {
    render(
      <SideChatLayer
        windows={[windowState("a"), windowState("b")]}
        {...handlers()}
      />,
    );

    expect(screen.getByRole("tablist", { name: "Open side chats" })).toBeInTheDocument();
    expect(screen.getByTestId("window-a")).toHaveAttribute("data-docked", "yes");
  });

  it("shows the most recently touched thread first", () => {
    render(
      <SideChatLayer
        windows={[windowState("a"), windowState("b")]}
        {...handlers()}
      />,
    );

    const tabs = screen.getAllByRole("tab");
    expect(tabs[1]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveAttribute("aria-selected", "false");
  });

  it("switches threads without unmounting the one left behind", () => {
    const props = handlers();
    render(
      <SideChatLayer
        windows={[windowState("a"), windowState("b")]}
        {...props}
      />,
    );

    fireEvent.click(screen.getAllByRole("tab")[0]!);

    expect(screen.getAllByRole("tab")[0]).toHaveAttribute(
      "aria-selected",
      "true",
    );
    // Still mounted: an answer streaming into thread b must not be abandoned.
    expect(screen.getByTestId("window-b")).toBeInTheDocument();
    expect(props.onFocus).toHaveBeenCalledWith("a");
  });

  it("closes a thread from its tab", () => {
    const props = handlers();
    render(<SideChatLayer windows={[windowState("a")]} {...props} />);

    fireEvent.click(
      screen.getByRole("button", { name: "Close the thread a side chat" }),
    );

    expect(props.onClose).toHaveBeenCalledWith("a");
  });
});
