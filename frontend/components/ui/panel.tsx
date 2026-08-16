import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * A bounded surface. Replaces `Card`, which is the pattern the design brief
 * rejects by name — "prefer fine dividers, tonal surface changes, and typography
 * over nested cards".
 *
 * The difference is not cosmetic. A `Panel` carries one tonal step and nothing
 * else: no ring, no shadow, no second radius inside it. Panels do not nest — if
 * you find yourself putting one inside another, the inner one wants to be a
 * `Region` (a ruled band) instead.
 *
 * Separation follows the system's per-theme rule. Sumi ink has the value range
 * to separate planes tonally, so dark draws no edge at all. Warm paper does not
 * — everything above the canvas tops out at 1.14:1 — so light draws a hairline.
 * The border stays present-but-transparent in dark so the two themes keep
 * identical geometry.
 *
 * See docs/mugensei-design-system.md.
 */
function Panel({
  className,
  size = "default",
  ...props
}: React.ComponentProps<"div"> & { size?: "default" | "sm" }) {
  return (
    <div
      data-slot="panel"
      data-size={size}
      className={cn(
        "group/panel flex flex-col overflow-hidden rounded-lg bg-surface text-foreground",
        "border border-divider dark:border-transparent",
        "gap-(--panel-gap) py-(--panel-gap) [--panel-gap:--spacing(4)]",
        "data-[size=sm]:[--panel-gap:--spacing(3)]",
        className,
      )}
      {...props}
    />
  );
}

function PanelHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-header"
      className={cn(
        "@container/panel-header grid auto-rows-min items-start gap-1 px-(--panel-gap)",
        "has-data-[slot=panel-action]:grid-cols-[1fr_auto]",
        className,
      )}
      {...props}
    />
  );
}

/**
 * Sans, not serif. A panel title is the system naming a region, not the reader's
 * own material — that distinction is what makes the serif mean something.
 */
function PanelTitle({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-title"
      className={cn(
        "text-base leading-snug font-semibold group-data-[size=sm]/panel:text-sm",
        className,
      )}
      {...props}
    />
  );
}

function PanelDescription({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

function PanelAction({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-action"
      className={cn("col-start-2 row-span-2 row-start-1 self-start justify-self-end", className)}
      {...props}
    />
  );
}

function PanelContent({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-content"
      className={cn("px-(--panel-gap)", className)}
      {...props}
    />
  );
}

/**
 * The foot of a panel. Separated by a rule rather than a second fill, so the
 * panel keeps exactly one tonal step.
 */
function PanelFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="panel-footer"
      className={cn(
        "mt-(--panel-gap) flex items-center border-t border-divider px-(--panel-gap) pt-(--panel-gap)",
        className,
      )}
      {...props}
    />
  );
}

/**
 * A band of the page, bounded by rules rather than by a container.
 *
 * This is the primitive for everything a card used to be misused for: a section
 * of a screen that needs a boundary but is not a floating object. It has no
 * fill and no radius, so regions can sit directly against one another and read
 * as one composed surface instead of a stack of tiles.
 */
function Region({
  className,
  edge = "block",
  ...props
}: React.ComponentProps<"section"> & {
  /** Which edges carry a rule. `block` is top-and-bottom, the common case. */
  edge?: "block" | "top" | "bottom" | "none";
}) {
  return (
    <section
      data-slot="region"
      className={cn(
        "min-w-0",
        edge === "block" && "border-y border-divider",
        edge === "top" && "border-t border-divider",
        edge === "bottom" && "border-b border-divider",
        className,
      )}
      {...props}
    />
  );
}

export {
  Panel,
  PanelHeader,
  PanelFooter,
  PanelTitle,
  PanelAction,
  PanelDescription,
  PanelContent,
  Region,
};
