"use client";

import { DeckJobRow } from "@/components/decks/deck-row";
import type { DeckJob } from "@/lib/deck-types";

/**
 * Deck generation in progress, shown in the right region.
 *
 * This was a permanent second column that spent most of its life displaying
 * "No generation in progress" — a panel explaining that it had nothing to say.
 * As a region it is simply absent until there is work, which is the same rule
 * the evidence region follows: the space fills when there is something to put
 * in it, and is margin the rest of the time.
 */
export function GenerationActivity({
  working,
  recentlyFailed,
  onCancel,
  onRetry,
}: {
  working: DeckJob[];
  recentlyFailed: DeckJob[];
  onCancel: (job: DeckJob) => void;
  onRetry: (job: DeckJob) => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="flex shrink-0 flex-col gap-1 border-b border-divider px-4 py-3">
        <h2 className="text-eyebrow uppercase text-muted-foreground">
          Generation activity
        </h2>
        <p className="text-xs text-muted-foreground">
          Book extraction and AI generation keep running whether or not this page
          stays open.
        </p>
      </header>

      <ul className="min-h-0 flex-1 space-y-3 px-4 py-4">
        {working.map((job) => (
          <DeckJobRow key={job.job_id} job={job} onCancel={onCancel} />
        ))}
        {recentlyFailed.map((job) => (
          <DeckJobRow key={job.job_id} job={job} onRetry={onRetry} />
        ))}
      </ul>
    </div>
  );
}
