"use client";

import { useResizablePane } from "@/hooks/use-resizable-pane";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "asp:reading-pane-width";
const DEFAULT_PERCENT = 50;
const MIN_PERCENT = 35;
const MAX_PERCENT = 62;

/**
 * Conversation on the left, the right region beside it, with a draggable
 * divider.
 *
 * The divider is a real `separator` with keyboard support: dragging is the
 * obvious interaction but not the only one anybody has. Width is remembered,
 * because re-sizing the reading pane on every visit is a small tax on the
 * thing people do most.
 *
 * Two rules from the design system are structural here.
 *
 * The region is never unmounted while it has content. `hidden` is what closes
 * it. Conditionally rendering it used to remount the document viewer, which
 * restarted playback and re-fetched every authenticated image — the same class
 * of bug the wrapper comment below describes, one level in.
 *
 * Below `md` the region overlays the conversation rather than replacing it in
 * flow. A document deserves the whole screen on a phone, but the conversation
 * must still be mounted and visible the instant it closes; the previous
 * `hidden md:flex` on the canvas blanked it instead.
 */
export function SplitPane({
  children,
  aside,
  /** Closed but not discarded — the region keeps its state and its scroll. */
  asideHidden = false,
  /** Names the region for its current mode, rather than always "document". */
  asideLabel = "Source document",
}: {
  children: React.ReactNode;
  aside: React.ReactNode | null;
  asideHidden?: boolean;
  asideLabel?: string;
}) {
  const open = Boolean(aside) && !asideHidden;
  const { percent, containerRef, separatorProps } = useResizablePane({
    storageKey: STORAGE_KEY,
    edge: "right",
    label: "Resize the document pane",
    defaultPercent: DEFAULT_PERCENT,
    minPercent: MIN_PERCENT,
    maxPercent: MAX_PERCENT,
    enabled: open,
  });

  return (
    <div
      ref={containerRef}
      className="relative flex min-h-0 flex-1 overflow-hidden"
    >
      {/*
        Keep this wrapper mounted whether or not a document is open. Changing
        the parent tree here used to remount the entire conversation workspace;
        that restarted video/iframe playback and made every authenticated image
        fetch and decode again whenever a citation opened or closed the reader.
      */}
      <div className="flex min-w-0 flex-1">{children}</div>

      {aside ? (
        <>
          <div
            {...separatorProps}
            className={cn(
              "hidden w-1 shrink-0 cursor-col-resize bg-border transition-colors md:block",
              "hover:bg-primary focus-visible:bg-primary",
              !open && "md:hidden",
            )}
          />

          {/*
            `overflow-hidden` matters as much as the width: without it one long
            unbroken string inside the pane sets its own minimum and pushes the
            entire row wider than the viewport, which is what a signed URL in
            an error message did.

            The width is carried as a custom property so the mobile rule stays a
            plain class rather than an inline style fighting it.
          */}
          <aside
            aria-label={asideLabel}
            className={cn(
              "min-w-0 overflow-hidden bg-background",
              "absolute inset-0 z-docked md:static md:z-auto md:w-[var(--pane)] md:shrink-0",
              !open && "hidden",
            )}
            style={{ "--pane": `${percent}%` } as React.CSSProperties}
          >
            {aside}
          </aside>
        </>
      ) : null}
    </div>
  );
}
