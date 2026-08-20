"use client";

import { RotateCcw } from "lucide-react";
import Link from "next/link";

/** Enough to find your place, few enough to stay a shortcut and not a list. */
const SHOWN = 3;

export interface ContinueEntry {
  key: string;
  href: string;
  title: string;
  /** "p. 112 · 5 questions", "12:04 · 4 questions". */
  detail: string;
}

/**
 * Where you left off in the material itself, not in a conversation about it.
 *
 * The lecture library already offers to continue a *thread*. This offers to
 * continue a *source* — the same idea one layer down, and the one that matters
 * for source-first study, because what a reader wants back is the page they
 * were on and the questions they left in its margins.
 */
export function ContinueSessions({
  entries,
  heading = "Continue",
}: {
  entries: ContinueEntry[];
  heading?: string;
}) {
  if (entries.length === 0) return null;

  return (
    <nav aria-labelledby="continue-sessions-heading" className="flex flex-col gap-1">
      <p
        id="continue-sessions-heading"
        className="px-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        {heading}
      </p>
      <ul className="flex flex-col gap-1">
        {entries.slice(0, SHOWN).map((entry) => (
          <li key={entry.key}>
            <Link
              href={entry.href}
              className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-surface-hover"
            >
              <RotateCcw
                aria-hidden
                className="size-4 shrink-0 text-muted-foreground"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm leading-snug">
                  {entry.title}
                </span>
                <span className="block truncate font-mono text-xs text-muted-foreground">
                  {entry.detail}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** How a session's position and marks read in the band. */
export function sessionDetail(
  position: string | null,
  questionCount: number,
): string {
  const questions =
    questionCount === 1 ? "1 question" : `${questionCount} questions`;
  // A session with no position has not been read past its opening, so saying
  // "p. 1" would claim a place the reader never reached.
  return position ? `${position} · ${questions}` : questions;
}
