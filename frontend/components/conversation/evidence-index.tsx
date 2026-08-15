"use client";

import { ArrowUpRight, BookOpenCheck } from "lucide-react";

import { formatPath } from "@/lib/citations";
import { partitionByCitation } from "@/lib/references";
import type { CitationRef, EvidenceRef } from "@/lib/types";
import { cn } from "@/lib/utils";

export function EvidenceIndex({
  evidence,
  citations,
  activePage,
  onOpen,
}: {
  evidence: EvidenceRef[];
  citations: CitationRef[];
  activePage: number;
  onOpen: (reference: EvidenceRef, page?: number) => void;
}) {
  const cited = partitionByCitation(evidence, citations).cited;

  return (
    <aside
      aria-label="Evidence index"
      className="hidden h-full w-40 shrink-0 overflow-y-auto border-l border-border bg-background/80 px-3 py-5 xl:block"
    >
      <h2 className="text-[0.68rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        Evidence index
      </h2>
      <p className="mt-4 text-xs text-muted-foreground">
        {cited.length} {cited.length === 1 ? "citation" : "citations"} in this answer
      </p>

      {cited.length > 0 ? (
        <ol className="mt-4 space-y-2.5">
          {cited.map((reference) => {
            const index = evidence.indexOf(reference) + 1;
            const page =
              citations.find(
                (citation) =>
                  citation.node_id === reference.node_id &&
                  (citation.book_id == null || citation.book_id === reference.book_id),
              )?.page ?? reference.pages[0] ?? 1;
            const active = page === activePage;
            const path = formatPath(reference.path);
            return (
              <li key={`${reference.book_id}-${reference.node_id}-${page}`}>
                <button
                  type="button"
                  onClick={() => onOpen(reference, page)}
                  aria-current={active ? "true" : undefined}
                  className={cn(
                    "group w-full rounded-lg border px-3 py-3 text-left transition-colors",
                    active
                      ? "border-positive bg-positive-muted/55"
                      : "border-border bg-card/45 hover:border-primary/60 hover:bg-accent",
                  )}
                >
                  <span className="flex items-center gap-1.5 text-xs font-medium text-positive">
                    <span className="font-mono">{index}</span>
                    Page {page}
                    <ArrowUpRight className="ml-auto size-3.5 opacity-60 group-hover:opacity-100" aria-hidden />
                  </span>
                  <span className="mt-2 block text-xs leading-relaxed text-foreground">
                    {path.at(-1) ?? "Cited passage"}
                  </span>
                  {reference.excerpt ? (
                    <span className="mt-1 block line-clamp-2 text-[0.68rem] leading-relaxed text-muted-foreground">
                      {reference.excerpt}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ol>
      ) : (
        <div className="mt-8 text-center text-muted-foreground">
          <BookOpenCheck className="mx-auto size-5" aria-hidden />
          <p className="mt-2 text-xs leading-relaxed">Citations appear here when an answer is grounded.</p>
        </div>
      )}
    </aside>
  );
}
