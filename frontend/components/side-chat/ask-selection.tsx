"use client";

import { MessageSquarePlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

/** Ignore a stray click-drag that selects a character or two. */
const MINIMUM_SELECTION_CHARS = 3;

/** Gap between the selection and the button offered above it. */
const OFFSET_PX = 10;
const ESTIMATED_WIDTH_PX = 150;
const ESTIMATED_HEIGHT_PX = 32;

export interface Selected {
  text: string;
  turnIndex: number;
  /** Viewport coordinates for the button, already clamped on screen. */
  x: number;
  y: number;
}

/**
 * Read the current text selection, when it sits inside one recorded turn.
 *
 * Computed on pointer and key release rather than on every `selectionchange`,
 * because that fires continuously while the reader drags and the button would
 * chase the cursor. `selectionchange` is still watched, but only to dismiss:
 * the moment a selection collapses the offer is stale.
 *
 * A selection spanning two turns is refused rather than attributed to one of
 * them — there is no honest single turn to anchor it to.
 */
export function useSelectionToAsk(
  container: React.RefObject<HTMLElement | null>,
): { selected: Selected | null; dismiss: () => void } {
  const [selected, setSelected] = useState<Selected | null>(null);

  const dismiss = useCallback(() => setSelected(null), []);

  const read = useCallback(() => {
    const root = container.current;
    const selection = document.getSelection();
    if (!root || !selection || selection.isCollapsed || !selection.rangeCount) {
      setSelected(null);
      return;
    }
    const text = selection.toString().trim();
    if (text.length < MINIMUM_SELECTION_CHARS) {
      setSelected(null);
      return;
    }
    const range = selection.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) {
      setSelected(null);
      return;
    }

    const elementOf = (node: Node | null): Element | null =>
      node instanceof Element ? node : (node?.parentElement ?? null);
    const turnOf = (node: Node | null): string | null =>
      elementOf(node)
        ?.closest("[data-turn-index]")
        ?.getAttribute("data-turn-index") ?? null;

    const start = turnOf(range.startContainer);
    const end = turnOf(range.endContainer);
    if (start === null || start !== end) {
      setSelected(null);
      return;
    }
    // Only the answer body is a passage. Reference cards, the question bubble
    // and the row of controls are interface, and quoting them anchors nothing.
    if (!elementOf(range.startContainer)?.closest("[data-answer]")) {
      setSelected(null);
      return;
    }

    const bounds = range.getBoundingClientRect();
    if (!bounds.width && !bounds.height) {
      setSelected(null);
      return;
    }
    const middle = bounds.left + bounds.width / 2;
    const above = bounds.top - OFFSET_PX - ESTIMATED_HEIGHT_PX;
    setSelected({
      text,
      turnIndex: Number(start),
      x: Math.min(
        Math.max(middle, ESTIMATED_WIDTH_PX / 2 + 8),
        window.innerWidth - ESTIMATED_WIDTH_PX / 2 - 8,
      ),
      // Below the selection when there is no room above it.
      y: above > 8 ? above : bounds.bottom + OFFSET_PX,
    });
  }, [container]);

  useEffect(() => {
    const onSelectionChange = () => {
      const selection = document.getSelection();
      if (!selection || selection.isCollapsed) setSelected(null);
    };
    document.addEventListener("pointerup", read);
    document.addEventListener("keyup", read);
    document.addEventListener("selectionchange", onSelectionChange);
    return () => {
      document.removeEventListener("pointerup", read);
      document.removeEventListener("keyup", read);
      document.removeEventListener("selectionchange", onSelectionChange);
    };
  }, [read]);

  return { selected, dismiss };
}

export interface AskSelectionProps {
  container: React.RefObject<HTMLElement | null>;
  onAsk: (turnIndex: number, quotedText: string) => void;
}

/**
 * The offer that appears over a highlighted passage.
 *
 * This is the gesture the feature is really for: highlight the sentence that
 * raised the question, and ask about that sentence rather than about the whole
 * answer it sits in. The per-answer button beside Copy stays, because it is the
 * keyboard-reachable path and because sometimes the whole answer *is* the
 * subject.
 */
export function AskSelection({ container, onAsk }: AskSelectionProps) {
  const { selected, dismiss } = useSelectionToAsk(container);

  useEffect(() => {
    if (!selected) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [selected, dismiss]);

  if (!selected) return null;

  return (
    <button
      type="button"
      // The pointer goes down on this button while the selection still exists;
      // preventing default keeps the browser from clearing it before the click.
      onPointerDown={(event) => event.preventDefault()}
      onClick={() => {
        onAsk(selected.turnIndex, selected.text);
        document.getSelection()?.removeAllRanges();
        dismiss();
      }}
      style={{ left: selected.x, top: selected.y }}
      className="fixed z-drawer flex -translate-x-1/2 items-center gap-2 rounded-full border border-border bg-popover px-3 py-2 text-xs font-medium text-popover-foreground shadow-lg motion-safe:animate-in motion-safe:fade-in focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <MessageSquarePlus aria-hidden className="size-3.5" />
      Ask about this
    </button>
  );
}
