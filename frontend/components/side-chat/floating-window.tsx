"use client";

import { GripVertical, Minus, X } from "lucide-react";
import { useCallback, useEffect, useId, useRef } from "react";

import { Button } from "@/components/ui/button";
import {
  clampRect,
  isMoveKey,
  moveByKey,
  resizeByKey,
  snapRect,
  type WindowRect,
} from "@/lib/floating-window";
import { cn } from "@/lib/utils";

function viewport() {
  return { width: window.innerWidth, height: window.innerHeight };
}

export interface FloatingWindowProps {
  title: string;
  rect: WindowRect;
  onRectChange: (rect: WindowRect) => void;
  onMinimize: () => void;
  onClose: () => void;
  onFocus: () => void;
  zIndex: number;
  /** Rendered but not shown, so a streaming answer survives minimizing. */
  hidden?: boolean;
  isBusy?: boolean;
  children: React.ReactNode;
}

/**
 * One movable, resizable, non-modal window.
 *
 * Non-modal is the point: the reader keeps using the main conversation and
 * other windows while this one answers, so there is no focus trap and no
 * backdrop. It is still a labelled `dialog`, so assistive technology announces
 * it as a distinct region rather than as loose content at the end of the page.
 *
 * Both gestures are available from the keyboard, because a window that can only
 * be placed with a pointer is a window some readers cannot place at all. The
 * move and resize handles are buttons that respond to the arrow keys — Shift
 * for a larger step — and `Escape` anywhere inside minimizes.
 */
export function FloatingWindow({
  title,
  rect,
  onRectChange,
  onMinimize,
  onClose,
  onFocus,
  zIndex,
  hidden = false,
  isBusy = false,
  children,
}: FloatingWindowProps) {
  const titleId = useId();
  // A drag is a stream of moves against the rectangle the pointer went down
  // on; reading it from props mid-drag would lag a frame behind the pointer.
  const dragRef = useRef<{
    mode: "move" | "resize";
    pointerId: number;
    originX: number;
    originY: number;
    start: WindowRect;
  } | null>(null);
  // The pointer listeners are attached once, and read the current callback from
  // here. Keying their effect on `onRectChange` instead made dragging
  // impossible: the layer builds a new callback on every render, so the first
  // move re-rendered, the effect tore down, and its cleanup dropped the drag
  // that was in progress.
  const onRectChangeRef = useRef(onRectChange);
  useEffect(() => {
    onRectChangeRef.current = onRectChange;
  }, [onRectChange]);

  const beginDrag = useCallback(
    (mode: "move" | "resize") => (event: React.PointerEvent<HTMLButtonElement>) => {
      if (event.button !== 0) return;
      // `preventDefault` stops the drag from selecting text — and also stops
      // the handle being focused, which left the arrow keys operating on
      // whatever was focused before. Focusing explicitly means a window just
      // dragged with the mouse can be nudged from the keyboard straight after.
      event.preventDefault();
      event.currentTarget.focus();
      onFocus();
      dragRef.current = {
        mode,
        pointerId: event.pointerId,
        originX: event.clientX,
        originY: event.clientY,
        start: rect,
      };
      document.body.style.userSelect = "none";
      document.body.style.cursor = mode === "move" ? "grabbing" : "nwse-resize";
    },
    [onFocus, rect],
  );

  useEffect(() => {
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag || event.pointerId !== drag.pointerId) return;
      const dx = event.clientX - drag.originX;
      const dy = event.clientY - drag.originY;
      const next =
        drag.mode === "move"
          ? { ...drag.start, x: drag.start.x + dx, y: drag.start.y + dy }
          : {
              ...drag.start,
              width: drag.start.width + dx,
              height: drag.start.height + dy,
            };
      // Snap first, then clamp, so the clamp always has the last word: a drag
      // that would leave the viewport was previously only snapped, which let a
      // window be dragged off the right edge and off the bottom.
      const bounds = viewport();
      onRectChangeRef.current(clampRect(snapRect(next, bounds), bounds));
    };
    const onUp = () => {
      if (!dragRef.current) return;
      dragRef.current = null;
      document.body.style.removeProperty("user-select");
      document.body.style.removeProperty("cursor");
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      // Only runs on unmount now, which is when it matters: a window closed
      // mid-drag must not leave the whole page unselectable.
      dragRef.current = null;
      document.body.style.removeProperty("user-select");
      document.body.style.removeProperty("cursor");
    };
    // Attached once for the window's lifetime. See `onRectChangeRef`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleKeys = useCallback(
    (mode: "move" | "resize") => (event: React.KeyboardEvent) => {
      if (!isMoveKey(event.key)) return;
      event.preventDefault();
      const options = { large: event.shiftKey };
      onRectChange(
        mode === "move"
          ? moveByKey(rect, event.key, viewport(), options)
          : resizeByKey(rect, event.key, viewport(), options),
      );
    },
    [onRectChange, rect],
  );

  return (
    <section
      role="dialog"
      aria-modal={false}
      aria-labelledby={titleId}
      hidden={hidden}
      // Focusing anywhere inside raises the window, which is what a reader
      // means by clicking on it — including by tabbing into it.
      onFocusCapture={onFocus}
      onPointerDownCapture={onFocus}
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        onMinimize();
      }}
      style={{
        left: rect.x,
        top: rect.y,
        width: rect.width,
        height: rect.height,
        zIndex,
      }}
      className={cn(
        "fixed flex flex-col overflow-hidden rounded-xl border border-border bg-card shadow-xl",
        // Only the resize handle animates, and only when the reader allows it.
        "motion-safe:transition-shadow",
        hidden && "hidden",
      )}
    >
      <header className="flex shrink-0 items-center gap-1 border-b border-border bg-surface pr-1">
        <button
          type="button"
          aria-label={`Move the ${title} side chat. Use the arrow keys, or hold Shift for larger steps.`}
          onPointerDown={beginDrag("move")}
          onKeyDown={handleKeys("move")}
          className="flex min-w-0 flex-1 cursor-grab items-center gap-2 rounded-tl-xl px-2 py-2 text-left focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring active:cursor-grabbing"
        >
          <GripVertical
            aria-hidden
            className="size-3.5 shrink-0 text-muted-foreground"
          />
          <span
            id={titleId}
            className="truncate text-xs font-medium text-foreground"
          >
            {title}
          </span>
          {isBusy && (
            <span
              aria-hidden
              className="size-1.5 shrink-0 rounded-full bg-primary motion-safe:animate-pulse"
            />
          )}
        </button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Minimize the ${title} side chat`}
          onClick={onMinimize}
        >
          <Minus aria-hidden />
        </Button>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={`Close the ${title} side chat`}
          onClick={onClose}
        >
          <X aria-hidden />
        </Button>
      </header>

      <div className="flex min-h-0 flex-1 flex-col">{children}</div>

      <button
        type="button"
        aria-label={`Resize the ${title} side chat. Use the arrow keys, or hold Shift for larger steps.`}
        onPointerDown={beginDrag("resize")}
        onKeyDown={handleKeys("resize")}
        className="absolute bottom-0 right-0 size-4 cursor-nwse-resize rounded-br-xl focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
      >
        <span
          aria-hidden
          className="absolute bottom-1 right-1 size-2 border-b-2 border-r-2 border-divider"
        />
      </button>
    </section>
  );
}
