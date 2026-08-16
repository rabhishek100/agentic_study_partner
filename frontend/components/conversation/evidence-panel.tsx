"use client";

import { References } from "@/components/conversation/references";
import type { ChatTurn, EvidenceRef } from "@/lib/types";

/**
 * The sources behind one answer, shown in the right region.
 *
 * This is the direction's central idea made literal: the region is empty until
 * an answer is grounded, and then it fills. The page is asymmetric because
 * grounding is what occupies the space, not because a panel happens to live
 * there.
 *
 * It shows one turn at a time. A conversation has many answers and each has its
 * own evidence, so "the sources" is only meaningful with respect to a turn —
 * the newest one by default, or whichever the reader opened.
 */
export function EvidencePanel({
  turn,
  turnNumber,
  onOpenReference,
}: {
  turn: ChatTurn | null;
  /** 1-based, so the heading matches what the reader counted down the page. */
  turnNumber: number | null;
  onOpenReference: (reference: EvidenceRef, page?: number) => void;
}) {
  const result = turn?.result ?? null;
  const evidence = result?.evidence ?? [];
  const webSources = result?.web_sources ?? [];

  return (
    <div className="flex h-full min-h-0 flex-col overflow-y-auto">
      <header className="flex shrink-0 items-baseline gap-2 border-b border-divider px-4 py-3">
        <h2 className="text-eyebrow uppercase text-muted-foreground">Evidence</h2>
        {evidence.length > 0 ? (
          <span className="text-xs tabular-nums text-muted-foreground">
            {evidence.length} {evidence.length === 1 ? "source" : "sources"}
          </span>
        ) : null}
        {turnNumber ? (
          <span className="ml-auto text-xs tabular-nums text-muted-foreground">
            Answer {turnNumber}
          </span>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 px-4 py-4">
        {evidence.length === 0 && webSources.length === 0 ? (
          <p className="text-sm leading-relaxed text-muted-foreground">
            Nothing is grounded yet. When an answer cites its sources, they
            appear here beside it.
          </p>
        ) : (
          <References
            evidence={evidence}
            citations={result?.citations ?? []}
            webSources={webSources}
            onOpenReference={onOpenReference}
          />
        )}
      </div>
    </div>
  );
}
