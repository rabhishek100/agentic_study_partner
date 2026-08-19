import { cn } from "@/lib/utils";

/**
 * The Mugensei mark: scattered evidence routed into one understanding.
 *
 * Drawn from the seventh panel of the logo exploration. A loose field of small
 * squares sits on the left — some connected, some not, because a library
 * always holds more than any one answer draws on. Four traces leave the
 * connected ones, run horizontally, turn once at exactly 45 degrees, and enter
 * the same face of a single filled square. That square is the answer; the
 * traces are what makes it grounded rather than asserted.
 *
 * Things in it that are load-bearing:
 *
 * - The strays. Squares with no trace are the point, not decoration: evidence
 *   exists before it is retrieved, and a mark where everything connects would
 *   be claiming the opposite.
 * - One turn per trace, always 45 degrees. Circuit routing rather than a
 *   fanned bundle of straight lines — it reads as signal being carried, which
 *   is what retrieval is, and the shared angle is what makes four different
 *   paths look like one system.
 * - The traces converge on the terminal square's left face rather than merging
 *   into a trunk first. A trunk reads as a bracket or a merge icon; separate
 *   paths arriving at one place read as gathering.
 * - Jade throughout, including the terminal square. The panel puts the
 *   product's green at the point of resolution, so the vermilion seal is no
 *   longer in the mark — it stays the signature everywhere else it appears.
 *
 * The jade is the documented exception to "jade carries meaning": the mark is
 * identity, not state. See docs/mugensei-design-system.md.
 */
export function BrandMark({
  className,
  size = "md",
  /** Drops jade to the foreground, for one-colour contexts. */
  monochrome = false,
}: {
  className?: string;
  size?: "sm" | "md";
  monochrome?: boolean;
}) {
  const px = size === "sm" ? 24 : 40;
  const signal = monochrome ? "var(--foreground)" : "var(--action)";

  return (
    <svg
      aria-hidden
      focusable="false"
      width={px}
      height={px}
      viewBox="0 0 40 40"
      className={cn("shrink-0", className)}
    >
      {/*
        Every diagonal is a 45-degree run: the rise equals the reach. The two
        inner traces have no turn at all — they run straight in. Giving all
        four a diagonal fused them into one filled wedge at the square's face,
        which is the opposite of four paths arriving.
      */}
      <g
        fill="none"
        stroke={signal}
        strokeWidth={1.4}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M13 9H20.5L28.5 17" />
        <path d="M9.2 15.5H29" />
        <path d="M10.2 24.5H29" />
        <path d="M14 30.5H20.5L28.5 23" />
      </g>

      {/* The field. The first four carry a trace; the rest are the library. */}
      <g fill="var(--foreground)">
        <rect x="11.7" y="7.7" width="2.6" height="2.6" />
        <rect x="9.2" y="14.2" width="2.6" height="2.6" />
        <rect x="10.2" y="23.2" width="2.6" height="2.6" />
        <rect x="12.7" y="29.2" width="2.6" height="2.6" />
        <rect x="4" y="10.6" width="2.6" height="2.6" />
        <rect x="6.2" y="19.4" width="2.6" height="2.6" />
        <rect x="4.6" y="26.9" width="2.6" height="2.6" />
        <rect x="16.4" y="12.6" width="2.6" height="2.6" />
        <rect x="17.2" y="25.9" width="2.6" height="2.6" />
      </g>

      <rect x="29" y="16" width="8" height="8" fill={signal} />
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
