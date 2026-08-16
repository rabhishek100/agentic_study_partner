import { cn } from "@/lib/utils";

/**
 * A placeholder for content whose shape is known before it arrives.
 *
 * Deliberately static. The motion rules allow exactly one looping animation in
 * the system — the spinner, for work of unknown duration — and a pulsing block
 * behind a reading surface is the ambient motion the brief rules out. A skeleton
 * says "something rectangular goes here"; it does not need to shimmer to say it.
 *
 * Use it only where the shape is known and stable. Where it is not, the honest
 * control is a spinner with a label.
 *
 * Wrap a group of these in a container carrying `aria-busy="true"` — or pass
 * `busy` on a single one — so the state is announced rather than merely drawn.
 */
function Skeleton({
  className,
  busy,
  ...props
}: React.ComponentProps<"div"> & { busy?: boolean }) {
  return (
    <div
      data-slot="skeleton"
      aria-busy={busy ? true : undefined}
      aria-hidden={busy ? undefined : true}
      className={cn("rounded-md bg-surface-hover", className)}
      {...props}
    />
  );
}

export { Skeleton };
