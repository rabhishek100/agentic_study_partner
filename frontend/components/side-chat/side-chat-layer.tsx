"use client";

import { AlertCircle, MessageSquare, X } from "lucide-react";
import { useEffect, useState } from "react";

import { SideChatWindow } from "@/components/side-chat/side-chat-window";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import type { SideChatWindow as SideChatWindowState } from "@/hooks/use-side-chats";
import { FLOATING_MEDIA_QUERY } from "@/lib/floating-window";
import type { SideChatSurface, SideChatTurn } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";
import type { WindowRect } from "@/lib/floating-window";
import { cn } from "@/lib/utils";

/** Above the app chrome, below dialogs and the reading pane's own overlays. */
const BASE_Z_INDEX = 30;

/**
 * Whether this viewport can carry floating windows at all.
 *
 * Dragging needs a pointer and room. Below the threshold the same windows are
 * rendered as one docked sheet with a tab strip, which is a better narrow-screen
 * experience than a draggable panel covering the conversation it refers to.
 */
export function useFloatingCapable(): boolean {
  const [capable, setCapable] = useState(true);

  useEffect(() => {
    const query = window.matchMedia(FLOATING_MEDIA_QUERY);
    const update = () => setCapable(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return capable;
}

export interface SideChatLayerProps {
  windows: SideChatWindowState[];
  onRectChange: (sideChatId: string, rect: WindowRect) => void;
  onMinimize: (sideChatId: string, minimized: boolean) => void;
  onClose: (sideChatId: string) => void;
  onFocus: (sideChatId: string) => void;
  onSettled: (sideChatId: string, recorded: boolean) => void;
  onAnchorsChange: (sideChatId: string, anchors: Anchor[]) => void;
  surface: SideChatSurface;
  renderTurns: (state: {
    turns: SideChatTurn<unknown>[];
    isLoading: boolean;
    isQueued: boolean;
  }) => React.ReactNode;
  resolveQuoteTurn: (text: string) => number | null;
  /** Clears the question a window was opened with, once it has asked it. */
  onPendingSent?: (sideChatId: string) => void;
  /** Reported here because a side chat that failed to open has no window. */
  error?: string;
  onDismissError?: () => void;
}

function SideChatError({
  error,
  onDismiss,
}: {
  error: string;
  onDismiss?: () => void;
}) {
  return (
    <div className="fixed bottom-3 left-1/2 z-drawer w-[min(90vw,28rem)] -translate-x-1/2">
      <Alert variant="destructive" className="bg-card shadow-lg">
        <AlertCircle aria-hidden />
        <AlertDescription className="flex items-start gap-2">
          <span className="min-w-0 flex-1">{error}</span>
          {onDismiss && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Dismiss this message"
              onClick={onDismiss}
            >
              <X aria-hidden />
            </Button>
          )}
        </AlertDescription>
      </Alert>
    </div>
  );
}

/**
 * Every open side chat, plus the dock of the ones put away.
 *
 * List order is stacking order, so the window the reader touched last is the
 * one on top. Minimized windows stay mounted and hidden rather than being
 * unmounted, because their answers are still arriving.
 */
export function SideChatLayer({
  windows,
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
  error,
  onDismissError,
}: SideChatLayerProps) {
  const floating = useFloatingCapable();
  const [activeDocked, setActiveDocked] = useState<string | null>(null);

  const minimized = windows.filter((entry) => entry.minimized);

  // On a narrow viewport one thread is visible at a time; default to the most
  // recently touched, which is the last in stacking order.
  const dockedActive =
    windows.find((entry) => entry.sideChat.conversation_id === activeDocked) ??
    windows.at(-1) ??
    null;

  // An error with no windows is the important case: the side chat could not be
  // opened at all, so this is the only thing the reader has to go on.
  if (windows.length === 0) {
    return error ? (
      <SideChatError error={error} onDismiss={onDismissError} />
    ) : null;
  }

  if (!floating) {
    return (
      <div className="fixed inset-x-0 bottom-0 z-floating-window flex h-[70dvh] flex-col border-t border-border bg-card shadow-2xl">
        <div
          role="tablist"
          aria-label="Open side chats"
          className="flex shrink-0 items-center gap-1 overflow-x-auto border-b border-border px-1 py-1"
        >
          {windows.map((entry) => {
            const id = entry.sideChat.conversation_id;
            const isActive = dockedActive?.sideChat.conversation_id === id;
            return (
              <div key={id} className="flex shrink-0 items-center">
                <button
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => {
                    setActiveDocked(id);
                    onFocus(id);
                  }}
                  className={cn(
                    "max-w-40 truncate rounded-md px-2 py-1 text-xs",
                    isActive
                      ? "bg-secondary text-secondary-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  {entry.sideChat.title}
                  {entry.unread && (
                    <span
                      aria-label="has a new answer"
                      className="ml-1 inline-block size-1.5 rounded-full bg-primary align-middle"
                    />
                  )}
                </button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Close the ${entry.sideChat.title} side chat`}
                  onClick={() => onClose(id)}
                >
                  <X aria-hidden />
                </Button>
              </div>
            );
          })}
        </div>

        {/*
          Every thread stays mounted here too, so switching tabs does not
          abandon an answer that is still streaming in another one.
        */}
        {windows.map((entry) => {
          const id = entry.sideChat.conversation_id;
          const isActive = dockedActive?.sideChat.conversation_id === id;
          return (
            <div
              key={id}
              hidden={!isActive}
              className={cn(
                "min-h-0 flex-1 flex-col",
                isActive ? "flex" : "hidden",
              )}
            >
              <SideChatWindow
                docked
                window={entry}
                zIndex={BASE_Z_INDEX}
                onRectChange={(rect) => onRectChange(id, rect)}
                onMinimize={() => onMinimize(id, true)}
                onClose={() => onClose(id)}
                onFocus={() => onFocus(id)}
                onSettled={(recorded) => onSettled(id, recorded)}
                onAnchorsChange={(anchors) => onAnchorsChange(id, anchors)}
                surface={surface}
                renderTurns={renderTurns}
                resolveQuoteTurn={resolveQuoteTurn}
                onPendingSent={() => onPendingSent?.(id)}
              />
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <>
      {error && <SideChatError error={error} onDismiss={onDismissError} />}
      {windows.map((entry, index) => {
        const id = entry.sideChat.conversation_id;
        return (
          <SideChatWindow
            key={id}
            window={entry}
            zIndex={BASE_Z_INDEX + index}
            onRectChange={(rect) => onRectChange(id, rect)}
            onMinimize={() => onMinimize(id, true)}
            onClose={() => onClose(id)}
            onFocus={() => onFocus(id)}
            onSettled={(recorded) => onSettled(id, recorded)}
            onAnchorsChange={(anchors) => onAnchorsChange(id, anchors)}
            surface={surface}
            renderTurns={renderTurns}
            resolveQuoteTurn={resolveQuoteTurn}
            onPendingSent={() => onPendingSent?.(id)}
          />
        );
      })}

      {minimized.length > 0 && (
        <div
          className="fixed bottom-3 right-3 z-floating-window flex max-w-[min(90vw,32rem)] flex-wrap items-center justify-end gap-2"
          aria-label="Minimized side chats"
        >
          {minimized.map((entry) => {
            const id = entry.sideChat.conversation_id;
            return (
              <Button
                key={id}
                variant="outline"
                size="sm"
                className="max-w-56 shadow-lg"
                title={entry.sideChat.title}
                onClick={() => onMinimize(id, false)}
              >
                <MessageSquare aria-hidden />
                <span className="truncate">{entry.sideChat.title}</span>
                {entry.unread && (
                  <span
                    aria-label="has a new answer"
                    className="size-1.5 shrink-0 rounded-full bg-primary"
                  />
                )}
              </Button>
            );
          })}
        </div>
      )}
    </>
  );
}
