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
  /**
   * Shown alongside rather than asked for — evidence that fills the region as
   * soon as an answer is grounded, activity that appears while decks build.
   *
   * These do not overlay at `compact`. They used to: the region became a
   * full-bleed overlay below `md` with no way out of it, so on a phone the
   * first grounded answer replaced the conversation permanently — no composer,
   * no turns, no dismiss control, because nothing had ever *opened* the panel
   * for there to be a control to close. A requested region (a document, a
   * session's questions) still overlays, and carries its own way back.
   */
  ambient?: boolean;
  /**
   * At `compact`, cover the lower part of the canvas instead of all of it.
   *
   * The design system's compact rule for the right region is "becomes a bottom
   * sheet", and which regions that suits is a property of what is behind them.
   * A page or a slide wants the whole screen. A lecture does not: its player is
   * 16:9 and about a quarter of a portrait phone, so a full overlay hid the
   * video the questions are about, and closing the panel left most of the
   * screen empty instead.
   */
  compactSheet?: boolean;
  /**
   * The draggable range for this mode, when it has one.
   *
   * Per mode, not per pane: the design system says as much, and the reason is
   * concrete. A document beside a conversation wants half the row; a
   * conversation beside a document wants a quarter of it, and a shared
   * default gives whichever mode opens second the other one's width. Its own
   * storage key for the same reason — one key means dragging the reading
   * column also moves the transcript.
   */
  resize?: {
    storageKey: string;
    defaultPercent: number;
    minPercent: number;
    maxPercent: number;
    /** What the divider is called, when "the document pane" is wrong. */
    label?: string;
  };
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
  const range = activeRegion?.resize;

  const { percent, containerRef, separatorProps } = useResizablePane({
    storageKey: range?.storageKey ?? STORAGE_KEY,
    edge: "right",
    label: range?.label ?? "Resize the document pane",
    defaultPercent: range?.defaultPercent ?? DEFAULT_PERCENT,
    minPercent: range?.minPercent ?? MIN_PERCENT,
    maxPercent: range?.maxPercent ?? MAX_PERCENT,
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
                  region.compactSheet &&
                    "max-md:top-1/3 max-md:border-t max-md:border-border",
                  region.ambient && "max-md:hidden",
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
