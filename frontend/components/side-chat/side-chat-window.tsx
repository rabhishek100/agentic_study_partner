"use client";

import { useEffect, useRef, useState } from "react";

import { AnchorEditor } from "@/components/side-chat/anchor-editor";
import { FloatingWindow } from "@/components/side-chat/floating-window";
import { SideChatComposer } from "@/components/side-chat/side-chat-composer";
import { useSideChat } from "@/hooks/use-side-chat";
import { useScrollAnchor } from "@/hooks/use-scroll-anchor";
import type { SideChatWindow as SideChatWindowState } from "@/hooks/use-side-chats";
import type { SideChatSurface, SideChatTurn } from "@/lib/side-chat";
import type { Anchor, ResponseDepth } from "@/lib/types";
import type { WindowRect } from "@/lib/floating-window";

export interface SideChatWindowProps {
  window: SideChatWindowState;
  zIndex: number;
  onRectChange: (rect: WindowRect) => void;
  onMinimize: () => void;
  onClose: () => void;
  onFocus: () => void;
  /** `recorded` is false for a turn that failed or was stopped. */
  onSettled: (recorded: boolean) => void;
  onAnchorsChange: (anchors: Anchor[]) => void;
  surface: SideChatSurface;
  /**
   * How this surface draws its exchanges. A lecture answer seeks a player and
   * opens slides; a book answer opens a page. The window owns neither.
   */
  renderTurns: (state: {
    turns: SideChatTurn<unknown>[];
    isLoading: boolean;
    isQueued: boolean;
  }) => React.ReactNode;
  resolveQuoteTurn: (text: string) => number | null;
  /**
   * Called once the window has asked the question it was opened with, so the
   * state that says "ask this" can be cleared from outside it.
   */
  onPendingSent?: () => void;
  /** The reader's standing instruction to answer from this source or abstain. */
  stayInSource?: boolean;
  /** Renders inside a docked sheet instead of a floating window. */
  docked?: boolean;
}

/**
 * One side chat: its own turns, its own stream, its own window.
 *
 * The component stays mounted while minimized rather than unmounting, so an
 * answer keeps arriving in a window the reader put away — which is the whole
 * point of being able to ask several questions at once. Minimizing therefore
 * hides it; only closing tears the stream down.
 */
export function SideChatWindow({
  window: state,
  zIndex,
  onRectChange,
  onMinimize,
  onClose,
  onFocus,
  onSettled,
  onAnchorsChange,
  surface,
  renderTurns,
  resolveQuoteTurn,
  onPendingSent,
  stayInSource = false,
  docked = false,
}: SideChatWindowProps) {
  const { sideChat } = state;
  const { turns, isStreaming, isQueued, isLoading, send, stop } =
    useSideChat<unknown>(sideChat.conversation_id, surface, stayInSource);
  const [responseDepth, setResponseDepth] = useState<ResponseDepth>("quick");
  const { viewportRef, contentRef, scrollToBottom } = useScrollAnchor<
    HTMLDivElement,
    HTMLDivElement
  >();

  // Report the moment a turn stops streaming, so a minimized window can show
  // that its answer has landed. Streaming state is the signal rather than turn
  // count, because a failed turn is also finished.
  const wasStreaming = useRef(false);
  const latest = turns.at(-1);
  useEffect(() => {
    if (wasStreaming.current && !isStreaming) {
      onSettled(latest?.status === "complete");
    }
    wasStreaming.current = isStreaming;
    // `latest` is read only at the moment streaming stops; depending on it
    // would re-run this on every streamed token.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isStreaming, onSettled]);

  useEffect(() => {
    if (!state.minimized) scrollToBottom("auto");
  }, [state.minimized, turns.length, scrollToBottom]);

  /**
   * Ask the question this window was opened with, once.
   *
   * Waits for the thread's history to load: `send` appends to `turns`, and
   * sending into a list that is about to be replaced by the fetched history
   * loses the turn from view while it streams. The ref guards against a second
   * send if the effect re-runs before the clear lands.
   */
  const askedRef = useRef<string | null>(null);
  const pending = state.pendingQuestion;
  useEffect(() => {
    if (!pending || isLoading) return;
    if (askedRef.current === pending) return;
    askedRef.current = pending;
    void send(pending, responseDepth);
    onPendingSent?.();
    // `responseDepth` is read at the moment of asking; a later change to it
    // must not re-ask the question.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, isLoading, send, onPendingSent]);

  const body = (
    // The window is its own typographic context: `side-chat-body` is the query
    // container and `side-chat-type` carries the scale, so the prose and the
    // chrome inside both follow the size the reader dragged it to.
    <div className="side-chat-body flex min-h-0 flex-1 flex-col">
      <div className="side-chat-type flex min-h-0 flex-1 flex-col">
        <AnchorEditor
          anchors={sideChat.anchors}
          onChange={onAnchorsChange}
          resolveTurn={resolveQuoteTurn}
        />
        <div
          ref={viewportRef}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
        >
          <div ref={contentRef}>
            {renderTurns({ turns, isLoading, isQueued })}
          </div>
        </div>
        <SideChatComposer
          isStreaming={isStreaming}
          responseDepth={responseDepth}
          onResponseDepthChange={setResponseDepth}
          onSubmit={(question) => send(question, responseDepth)}
          onStop={stop}
          showDepth={surface.supportsDepth}
          label={sideChat.title}
        />
      </div>
    </div>
  );

  if (docked) {
    return (
      <section
        aria-label={`${sideChat.title} side chat`}
        className="flex min-h-0 flex-1 flex-col"
      >
        {body}
      </section>
    );
  }

  return (
    <FloatingWindow
      title={sideChat.title}
      rect={state.rect}
      onRectChange={onRectChange}
      onMinimize={onMinimize}
      onClose={onClose}
      onFocus={onFocus}
      zIndex={zIndex}
      hidden={state.minimized}
      isBusy={isStreaming}
    >
      {body}
    </FloatingWindow>
  );
}
