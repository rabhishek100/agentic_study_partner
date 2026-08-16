"use client";

import * as React from "react";
import { Progress as ProgressPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

/**
 * A determinate or indeterminate progress track.
 *
 * Pass a `value` for determinate progress. Omit it — or pass `null` — when the
 * work has started but its extent is not yet known: the track then draws as a
 * bare hairline with no fill, and `aria-valuenow` is omitted so assistive
 * technology reports "busy" rather than inventing a percentage.
 *
 * The indeterminate state does not animate. A looping shimmer here would be
 * decoration standing in for information, and the system permits exactly one
 * looping animation — the spinner. An empty track paired with the stage label
 * beside it says the same thing honestly.
 */
function Progress({
  className,
  value,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root>) {
  const indeterminate = value === null || value === undefined;

  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      data-state={indeterminate ? "indeterminate" : "determinate"}
      value={indeterminate ? null : value}
      className={cn(
        "relative flex h-1 w-full items-center overflow-x-hidden rounded-full",
        indeterminate ? "bg-divider" : "bg-surface-hover",
        className,
      )}
      {...props}
    >
      {indeterminate ? null : (
        <ProgressPrimitive.Indicator
          data-slot="progress-indicator"
          className="size-full flex-1 bg-primary transition-all"
          style={{ transform: `translateX(-${100 - (value || 0)}%)` }}
        />
      )}
    </ProgressPrimitive.Root>
  );
}

export { Progress };
