"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

const STORAGE_KEY = "asp:reading-pane-width";
const DEFAULT_PERCENT = 50;
const MIN_PERCENT = 35;
const MAX_PERCENT = 62;
const KEYBOARD_STEP = 5;

function clampPercent(value: number): number {
  return Math.min(MAX_PERCENT, Math.max(MIN_PERCENT, value));
}

/**
 * Conversation on the left, document on the right, with a draggable divider.
 *
 * The divider is a real `separator` with keyboard support: dragging is the
 * obvious interaction but not the only one anybody has. Width is remembered,
 * because re-sizing the reading pane on every visit is a small tax on the
 * thing people do most.
 */
export function SplitPane({
  children,
  aside,
}: {
  children: React.ReactNode;
  aside: React.ReactNode | null;
}) {
  const [percent, setPercent] = useState(DEFAULT_PERCENT);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  useEffect(() => {
    const stored = Number(window.localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(stored) && stored > 0) {
      setPercent(clampPercent(stored));
    }
  }, []);

  const store = useCallback((value: number) => {
    window.localStorage.setItem(STORAGE_KEY, String(Math.round(value)));
  }, []);

  useEffect(() => {
    if (!aside) return;

    const onMove = (event: PointerEvent) => {
      if (!draggingRef.current || !containerRef.current) return;
      const bounds = containerRef.current.getBoundingClientRect();
      const fromRight = bounds.right - event.clientX;
      const next = clampPercent((fromRight / bounds.width) * 100);
      setPercent(next);
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
      setPercent((current) => {
        store(current);
        return current;
      });
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      draggingRef.current = false;
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
    };
  }, [aside, store]);

  if (!aside) {
    return <div className="flex min-h-0 flex-1">{children}</div>;
  }

  return (
    <div ref={containerRef} className="flex min-h-0 flex-1 overflow-hidden">
      {/* Below the tablet breakpoint the document takes the whole area; a
          40% column on a phone is unreadable for both halves. */}
      <div className="hidden min-w-0 flex-1 md:flex">{children}</div>

      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize the document pane"
        aria-valuenow={Math.round(percent)}
        aria-valuemin={MIN_PERCENT}
        aria-valuemax={MAX_PERCENT}
        tabIndex={0}
        onPointerDown={(event) => {
          event.preventDefault();
          draggingRef.current = true;
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }}
        onKeyDown={(event) => {
          const direction =
            event.key === "ArrowLeft" ? 1 : event.key === "ArrowRight" ? -1 : 0;
          if (!direction) return;
          event.preventDefault();
          setPercent((current) => {
            const next = clampPercent(current + direction * KEYBOARD_STEP);
            store(next);
            return next;
          });
        }}
        className={cn(
          "hidden w-1 shrink-0 cursor-col-resize bg-border transition-colors md:block",
          "hover:bg-primary focus-visible:bg-primary",
        )}
      />

      {/*
        Full width below the breakpoint, a share of the row above it — carried
        as a custom property so the mobile rule is a plain class rather than an
        inline style fighting it.

        `overflow-hidden` matters as much as the width: without it one long
        unbroken string inside the pane sets its own minimum and pushes the
        entire row wider than the viewport, which is what a signed URL in an
        error message did.
      */}
      <aside
        aria-label="Source document"
        className="w-full min-w-0 shrink-0 overflow-hidden md:w-[var(--pane)]"
        style={{ "--pane": `${percent}%` } as React.CSSProperties}
      >
        {aside}
      </aside>
    </div>
  );
}
