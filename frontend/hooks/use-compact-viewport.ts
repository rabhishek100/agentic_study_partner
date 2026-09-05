"use client";

import { useEffect, useState } from "react";

/**
 * The `compact` boundary from the design system, in `em`.
 *
 * `em` rather than a device width, and the same number Tailwind's `md` step
 * uses, so a class-based rule and a measured one cannot disagree about where
 * the frame dissolves. In a media query `em` resolves against the browser's
 * default font size — the reader's own setting — so this asks "has this reader
 * made text bigger?", not "what device is this?".
 */
export const COMPACT_MAX_EM = 48;

/** Everything below `compact`; the 0.0625em is one step under the boundary. */
export const COMPACT_MEDIA_QUERY = `(max-width: ${COMPACT_MAX_EM - 0.0625}em)`;

/**
 * Whether the frame has dissolved to one task.
 *
 * `false` until the client mounts, because the server cannot measure a
 * viewport and seeding state from a guess hydrates against a different value.
 * Use it for decisions a class cannot express — what a panel *defaults* to —
 * and prefer a `max-md:` class for anything that is only presentation.
 */
export function useCompactViewport(): boolean {
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    const query = window.matchMedia(COMPACT_MEDIA_QUERY);
    const update = () => setCompact(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return compact;
}
