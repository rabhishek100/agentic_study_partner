"use client";

import { BookOpen, ChevronRight } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { formatPages, formatPath } from "@/lib/citations";
import type { EvidenceRef } from "@/lib/types";
import { cn } from "@/lib/utils";

function ReferenceCard({
  reference,
  index,
  onOpen,
}: {
  reference: EvidenceRef;
  index: number;
  onOpen?: (reference: EvidenceRef) => void;
}) {
  const [open, setOpen] = useState(false);
  const parts = formatPath(reference.path);

  return (
    <li className="rounded-lg border border-border bg-card">
      <div className="flex gap-3 p-3">
        <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded bg-citation-muted text-[0.7rem] font-semibold tabular-nums text-citation">
          {index}
        </span>

        <div className="min-w-0 flex-1 space-y-1">
          <nav aria-label="Location in the book" className="flex flex-wrap items-center gap-x-1 text-sm">
            {parts.map((part, position) => (
              <span key={`${part}-${position}`} className="flex items-center gap-x-1">
                {position > 0 && (
                  <ChevronRight
                    className="size-3 text-muted-foreground"
                    aria-hidden
                  />
                )}
                <span
                  className={cn(
                    position === parts.length - 1
                      ? "font-medium text-foreground"
                      : "text-muted-foreground",
                  )}
                >
                  {part}
                </span>
              </span>
            ))}
          </nav>

          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>{formatPages(reference.pages)}</span>
            {reference.retrieval_method && (
              <Badge variant="outline" className="font-normal">
                {reference.retrieval_method}
              </Badge>
            )}
            {reference.score != null && (
              <span className="tabular-nums">
                score {reference.score.toFixed(3)}
              </span>
            )}
          </p>

          {reference.excerpt && (
            <Collapsible open={open} onOpenChange={setOpen}>
              <CollapsibleTrigger className="text-xs font-medium text-citation hover:underline">
                {open ? "Hide the passage" : "Show the passage"}
              </CollapsibleTrigger>
              <CollapsibleContent>
                <blockquote className="mt-2 border-l-2 border-citation/40 pl-3 font-serif text-sm leading-relaxed text-muted-foreground">
                  {reference.excerpt}
                </blockquote>
              </CollapsibleContent>
            </Collapsible>
          )}

          {onOpen && (
            <button
              type="button"
              onClick={() => onOpen(reference)}
              className="text-xs font-medium text-citation hover:underline"
            >
              Open in the book
            </button>
          )}
        </div>
      </div>
    </li>
  );
}

export interface ReferencesProps {
  evidence: EvidenceRef[];
  onOpenReference?: (reference: EvidenceRef) => void;
}

export function References({ evidence, onOpenReference }: ReferencesProps) {
  if (evidence.length === 0) return null;

  // Group by book so a cross-book answer says which book each page is in.
  // With one book the grouping header would be noise, so it is dropped.
  const books = new Map<string, { title: string; items: EvidenceRef[] }>();
  for (const reference of evidence) {
    const key = String(reference.book_id ?? "unknown");
    const existing = books.get(key);
    if (existing) existing.items.push(reference);
    else
      books.set(key, {
        title: reference.book_title ?? "This book",
        items: [reference],
      });
  }
  const grouped = [...books.values()];
  const showBookHeadings = grouped.length > 1;

  return (
    <section
      aria-labelledby="references-heading"
      className="space-y-2 border-t border-border pt-4"
    >
      <h4
        id="references-heading"
        className="flex items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        <BookOpen className="size-3.5" aria-hidden />
        {evidence.length} {evidence.length === 1 ? "reference" : "references"}
      </h4>

      {grouped.map((group) => (
        <div key={group.title} className="space-y-2">
          {showBookHeadings && (
            <p className="text-xs font-medium text-foreground">{group.title}</p>
          )}
          <ul className="space-y-2">
            {group.items.map((reference) => (
              <ReferenceCard
                key={`${reference.node_id}-${reference.rank ?? reference.pages[0]}`}
                reference={reference}
                index={evidence.indexOf(reference) + 1}
                onOpen={onOpenReference}
              />
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
