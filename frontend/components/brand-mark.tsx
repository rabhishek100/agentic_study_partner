import { cn } from "@/lib/utils";

/**
 * The Mugensei mark: an open book, one continuous path rising out of the gutter
 * and leaving the frame, three nodes that grow as they rise, and the vermilion
 * seal.
 *
 * The concept is the brief's; the geometry is drawn rather than traced. A few
 * things in it are load-bearing:
 *
 * - The book is two *filled* planes, not stroked outlines. An outlined open book
 *   is indistinguishable from the Lucide book in the interface icon family, and
 *   a mark that reads as the eleventh icon is not a mark.
 * - The path ends on the top edge at y=0, where its cap is clipped. That is what
 *   makes "no terminus" true rather than asserted — a rounded cap short of the
 *   edge reads as a finial.
 * - The nodes carry no outline. Ringing them in the ground colour made them punch
 *   out of the ribbon, but it also cut the ribbon: the continuous path rendered
 *   as disconnected chunks with dots floating between them.
 * - One geometry at every size, so the mark is the same mark in the masthead and
 *   on the sign-in screen.
 *
 * The path uses jade, which is otherwise a semantic colour. That is the single
 * documented exception in the system: the mark is identity, not state.
 *
 * See docs/mugensei-design-system.md.
 */
export function BrandMark({
  className,
  size = "md",
  /** Drops jade and vermilion to the foreground, for one-colour contexts. */
  monochrome = false,
}: {
  className?: string;
  size?: "sm" | "md";
  monochrome?: boolean;
}) {
  const px = size === "sm" ? 24 : 40;

  /*
   * One geometry at every size. There were briefly two — a full mark and a
   * simplified one for 24px — and two different marks in one product reads as a
   * bug rather than as an optical adjustment.
   *
   * What made the small size fail was never the nodes. Each node carried a
   * ground-coloured ring so it would punch out of the ribbon, and those rings
   * severed the ribbon: "one continuous path" rendered as disconnected chunks
   * with dots floating between them, at every size. Without them the path stays
   * whole and the nodes read as swellings on it, which survives down to 24px —
   * the individual dots stop resolving there, but the path does not break, and a
   * continuous mark at small size is what matters.
   */
  return (
    <svg
      aria-hidden
      focusable="false"
      width={px}
      height={px}
      viewBox="0 0 40 40"
      className={cn("shrink-0", className)}
    >
      <g fill="var(--foreground)">
        <path d="M18.7 28.4C15.6 26.1 11 25.1 6 25.5v7.8c5-.4 9.6.6 12.7 3Z" />
        <path d="M20.1 28.4c3.1-2.3 7.7-3.3 12.7-2.9v7.8c-5-.4-9.6.6-12.7 3Z" />
      </g>
      <path
        d="M19.4 27.6C20.2 22.8 26.8 21.8 26.6 16.6 26.4 10.8 14 11.6 14.8 5.8 15.2 3 16.8 1.4 18 0"
        fill="none"
        stroke={monochrome ? "var(--foreground)" : "var(--action)"}
        strokeWidth={2}
        strokeLinecap="round"
      />
      <g fill="var(--foreground)">
        <circle cx="23.4" cy="22.3" r={2.4} />
        <circle cx="20.3" cy="11.2" r={2.4} />
        <circle cx="15.3" cy="3.9" r={2.4} />
      </g>
      <rect
        x={34.4}
        y={30.4}
        width={4.4}
        height={4.4}
        fill={monochrome ? "var(--foreground)" : "var(--seal)"}
      />
    </svg>
  );
}

/**
 * The horizontal lockup. The wordmark is the serif — literary rather than
 * corporate — and the symbol and wordmark each work on their own.
 */
export function BrandLockup({
  className,
  size = "sm",
}: {
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <BrandMark size={size} />
      <span
        className={cn(
          "font-serif font-medium tracking-tight",
          size === "sm" ? "text-base" : "text-xl",
        )}
      >
        Mugensei
      </span>
    </span>
  );
}
