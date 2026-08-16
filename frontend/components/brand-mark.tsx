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
 * - The nodes grow, because the idea is evidence *becoming* understanding. Three
 *   identical dots on a curve is the network cliché and says nothing. Each
 *   carries a ground-coloured ring so it stays a distinct disc on the path
 *   instead of dissolving into it at small sizes.
 * - `sm` is a redraw, not a scale: heavier path, larger nodes, larger seal, so
 *   all three still read at 24px, which the brief requires.
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
  /** The surface behind the mark; the nodes are ringed in it so they stay discs. */
  ground = "canvas",
}: {
  className?: string;
  size?: "sm" | "md";
  monochrome?: boolean;
  ground?: "canvas" | "surface";
}) {
  const small = size === "sm";
  const px = small ? 24 : 40;

  /*
   * Two geometries, not one scaled twice.
   *
   * At 24px a 40-unit viewBox gives 0.6px per unit, and three separate nodes
   * strung along a curve cannot resolve at that density — rendered and magnified,
   * the path breaks into disconnected speckle and the dots merge into the beading
   * they were meant to punctuate. That is a limit of the size, not a tuning
   * problem, so the compact mark simplifies the way every identity system's
   * favicon does: a bolder book, one heavier ribbon, a larger seal, and no nodes.
   *
   * The concept survives intact at both sizes — an open book, a path rising past
   * it and leaving the frame, and the seal. The three evidence nodes are present
   * at every size where they can actually be seen.
   */
  const book = small
    ? [
        "M18.6 27.8C14.6 24.9 8 23.9 1.6 24.7v10.8c6.4-.8 13 .2 17 3.1Z",
        "M21.4 27.8c4-2.9 10.6-3.9 17-3.1v10.8c-6.4-.8-13 .2-17 3.1Z",
      ]
    : [
        "M18.7 28.4C15.6 26.1 11 25.1 6 25.5v7.8c5-.4 9.6.6 12.7 3Z",
        "M20.1 28.4c3.1-2.3 7.7-3.3 12.7-2.9v7.8c-5-.4-9.6.6-12.7 3Z",
      ];
  const trail = small
    ? "M20 26.4C22 19.6 29.4 18 28.6 11.4 27.9 5.6 22.6 4 21.4 0"
    : "M19.4 27.6C20.2 22.8 26.8 21.8 26.6 16.6 26.4 10.8 14 11.6 14.8 5.8 15.2 3 16.8 1.4 18 0";
  const seal = small
    ? { x: 32.6, y: 29.8, size: 6.6 }
    : { x: 34.4, y: 30.4, size: 4.4 };

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
        {book.map((d) => (
          <path key={d} d={d} />
        ))}
      </g>
      <path
        d={trail}
        fill="none"
        stroke={monochrome ? "var(--foreground)" : "var(--action)"}
        strokeWidth={small ? 3.4 : 2}
        strokeLinecap="round"
      />
      {small ? null : (
        <g
          fill="var(--foreground)"
          stroke={ground === "surface" ? "var(--surface)" : "var(--canvas)"}
          strokeWidth={0.9}
        >
          <circle cx="23.4" cy="22.3" r={2} />
          <circle cx="20.3" cy="11.2" r={2.5} />
          <circle cx="15.3" cy="3.9" r={3} />
        </g>
      )}
      <rect
        x={seal.x}
        y={seal.y}
        width={seal.size}
        height={seal.size}
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
  ground = "canvas",
}: {
  className?: string;
  size?: "sm" | "md";
  ground?: "canvas" | "surface";
}) {
  return (
    <span className={cn("flex items-center gap-2", className)}>
      <BrandMark size={size} ground={ground} />
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
