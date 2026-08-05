"use client";

import { useResizablePane } from "@/hooks/use-resizable-pane";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "asp:reading-pane-width";
const DEFAULT_PERCENT = 50;
const MIN_PERCENT = 35;
const MAX_PERCENT = 62;

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
  const { percent, containerRef, separatorProps } = useResizablePane({
    storageKey: STORAGE_KEY,
    edge: "right",
    label: "Resize the document pane",
    defaultPercent: DEFAULT_PERCENT,
    minPercent: MIN_PERCENT,
    maxPercent: MAX_PERCENT,
    enabled: Boolean(aside),
  });

  if (!aside) {
    return <div className="flex min-h-0 flex-1">{children}</div>;
  }

  return (
    <div ref={containerRef} className="flex min-h-0 flex-1 overflow-hidden">
      {/* Below the tablet breakpoint the document takes the whole area; a
          40% column on a phone is unreadable for both halves. */}
      <div className="hidden min-w-0 flex-1 md:flex">{children}</div>

      <div
        {...separatorProps}
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
