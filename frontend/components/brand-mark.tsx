import { cn } from "@/lib/utils";

/**
 * The Mugensei mark: three pieces of evidence converging into one understanding.
 *
 * Scattered squares on the left are discrete and unconnected. Three lines carry
 * them right, meeting at exactly 45 degrees on a single filled square — the
 * point where separate evidence becomes connected understanding, which is the
 * brand's promise stated as geometry rather than illustrated.
 *
 * Things in it that are load-bearing:
 *
 * - The lines converge directly on the terminal square rather than merging into
 *   a shared trunk first. A trunk reads as a bracket or a merge icon; converging
 *   lines read as gathering.
 * - Three sources, not five or eight. Five tributaries arriving at an 8-unit
 *   square land about a pixel apart at 24px and blur into one blob.
 * - The terminal square is the vermilion seal. The brief keeps vermilion as a
 *   rare signature, and one small square at the point of resolution is exactly
 *   that — it also gives the composition its single focal point.
 * - One geometry at every size. The mark is the same mark in the masthead and on
 *   the sign-in screen.
 *
 * The jade is the one documented exception to "jade carries meaning": the mark
 * is identity, not state. See docs/mugensei-design-system.md.
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

  return (
    <svg
      aria-hidden
      focusable="false"
      width={px}
      height={px}
      viewBox="0 0 40 40"
      className={cn("shrink-0", className)}
    >
      {/* Optically centred: the content spans 2.8–35 before this nudge. */}
      <g transform="translate(1 0)">
        <g
          fill="none"
          stroke={monochrome ? "var(--foreground)" : "var(--action)"}
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M7.4 7.7H14.7L27 20" />
          <path d="M6.2 20H27" />
          <path d="M7.4 32.3H14.7L27 20" />
        </g>
        <g fill="var(--foreground)">
          <rect x="4" y="6" width="3.4" height="3.4" />
          <rect x="2.8" y="18.3" width="3.4" height="3.4" />
          <rect x="4" y="30.6" width="3.4" height="3.4" />
        </g>
        <rect
          x="27"
          y="16"
          width="8"
          height="8"
          fill={monochrome ? "var(--foreground)" : "var(--seal)"}
        />
      </g>
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
