"use client";

import { useResizablePane } from "@/hooks/use-resizable-pane";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "asp:reading-pane-width";
const DEFAULT_PERCENT = 50;
const MIN_PERCENT = 35;
const MAX_PERCENT = 62;

export interface RightRegionMode {
  key: string;
  node: React.ReactNode;
  /** Names the region for this mode, rather than one hardcoded label. */
  label: string;
  /**
   * Fixed width in pixels. Evidence and activity are reference columns and want
   * a stable width; a document wants a share of the row the reader can drag.
   */
  fixedWidth?: number;
}

/**
 * Conversation on the left, the right region beside it, with a draggable
 * divider when the active mode is resizable.
 *
 * The region holds several modes and shows one. Two rules from the design
 * system are structural here.
 *
 * **Every mode stays mounted.** `hidden` is what closes one. Conditionally
 * rendering them would remount the document viewer on every switch, throwing
 * away its rendered pages and re-fetching them — the same class of bug the
 * wrapper comment below records one level up.
 *
 * **Below `md` the region overlays the conversation** rather than replacing it
 * in flow. A document deserves the whole screen on a phone, but the conversation
 * must still be mounted and visible the instant it closes.
 */
export function SplitPane({
  children,
  regions = [],
  active = null,
}: {
  children: React.ReactNode;
  regions?: RightRegionMode[];
  /** The mode currently shown, or null for `none` — which is margin, not a panel. */
  active?: string | null;
}) {
  const activeRegion = regions.find((region) => region.key === active) ?? null;
  const resizable = Boolean(activeRegion) && !activeRegion?.fixedWidth;

  const { percent, containerRef, separatorProps } = useResizablePane({
    storageKey: STORAGE_KEY,
    edge: "right",
    label: "Resize the document pane",
    defaultPercent: DEFAULT_PERCENT,
    minPercent: MIN_PERCENT,
    maxPercent: MAX_PERCENT,
    enabled: resizable,
  });

  return (
    <div
      ref={containerRef}
      className="relative flex min-h-0 flex-1 overflow-hidden"
    >
      {/*
        Keep this wrapper mounted whether or not a region is open. Changing the
        parent tree here used to remount the entire conversation workspace; that
        restarted video/iframe playback and made every authenticated image fetch
        and decode again whenever a citation opened or closed the reader.
      */}
      <div className="flex min-w-0 flex-1">{children}</div>

      {regions.length > 0 ? (
        <>
          <div
            {...separatorProps}
            className={cn(
              "hidden w-1 shrink-0 cursor-col-resize bg-border transition-colors",
              "hover:bg-primary focus-visible:bg-primary",
              resizable && "md:block",
            )}
          />

          {regions.map((region) => {
            const isActive = region.key === active;
            return (
              <aside
                key={region.key}
                aria-label={region.label}
                className={cn(
                  // `overflow-hidden` matters as much as the width: without it one
                  // long unbroken string sets its own minimum and pushes the whole
                  // row wider than the viewport, which is what a signed URL in an
                  // error message once did.
                  "min-w-0 overflow-hidden bg-background",
                  "absolute inset-0 z-docked md:static md:z-auto md:shrink-0",
                  region.fixedWidth
                    ? "md:w-[var(--region-width)]"
                    : "md:w-[var(--pane)]",
                  !isActive && "hidden",
                )}
                style={
                  {
                    "--pane": `${percent}%`,
                    "--region-width": region.fixedWidth
                      ? `${region.fixedWidth}px`
                      : undefined,
                  } as React.CSSProperties
                }
              >
                {region.node}
              </aside>
            );
          })}
        </>
      ) : null}
    </div>
  );
}
