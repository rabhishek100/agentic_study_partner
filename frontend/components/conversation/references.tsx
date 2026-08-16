"use client";

import { BookOpen, ChevronDown, ChevronRight, Globe } from "lucide-react";
import { useState } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { formatPages } from "@/lib/citations";
import {
  groupByBook,
  partitionByCitation,
  pathBelow,
  type ReferenceGroup,
} from "@/lib/references";
import type { CitationRef, EvidenceRef, WebSourceRef } from "@/lib/types";
import { cn } from "@/lib/utils";

function ReferenceRow({
  reference,
  index,
  sharedPath,
  muted,
  onOpen,
}: {
  reference: EvidenceRef;
  index: number | null;
  sharedPath: string[];
  muted?: boolean;
  onOpen?: (reference: EvidenceRef, page?: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const parts = pathBelow(reference.path, sharedPath);
  const leaf = parts.at(-1) ?? reference.path;
  const ancestors = parts.slice(0, -1);

  return (
    <li className="border-b border-divider last:border-b-0">
      <div className="flex items-baseline gap-2 py-1.5">
        <span
          className={cn(
            "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded text-xs font-semibold tabular-nums",
            index === null
              ? "text-muted-foreground"
              : "bg-citation-muted text-citation",
          )}
        >
          {index ?? "·"}
        </span>

        <p className="min-w-0 flex-1 text-sm leading-snug">
          {ancestors.length > 0 && (
            <span className="text-muted-foreground">
              {ancestors.join(" › ")} ›{" "}
            </span>
          )}
          <span className={cn(muted ? "text-muted-foreground" : "font-medium")}>
            {leaf}
          </span>
          <span className="ml-2 whitespace-nowrap text-xs text-muted-foreground">
            {formatPages(reference.pages)}
          </span>
        </p>

        <div className="flex shrink-0 items-center gap-2">
          {reference.excerpt && (
            <button
              type="button"
              onClick={() => setOpen((current) => !current)}
              aria-expanded={open}
              className="text-xs text-muted-foreground transition-colors hover:text-citation"
            >
              {open ? "Hide" : "Passage"}
            </button>
          )}
          {onOpen && (
            <button
              type="button"
              onClick={() => onOpen(reference)}
              className="text-xs text-muted-foreground transition-colors hover:text-citation"
            >
              Open
            </button>
          )}
        </div>
      </div>

      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleContent>
          <blockquote className="mb-2 ml-6 border-l-2 border-evidence pl-3 font-serif text-xs leading-relaxed text-muted-foreground">
            {reference.excerpt}
          </blockquote>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function GroupHeading({
  group,
  showBook,
}: {
  group: ReferenceGroup;
  showBook: boolean;
}) {
  if (!showBook && group.sharedPath.length === 0) return null;

  return (
    <p className="flex flex-wrap items-baseline gap-x-1.5 text-xs text-muted-foreground">
      {showBook && (
        <span className="font-medium text-foreground">{group.bookTitle}</span>
      )}
      {group.sharedPath.length > 0 && (
        <span className="block w-full truncate" title={group.sharedPath.join(" › ")}>
          {showBook && "· "}
          {group.sharedPath.join(" › ")}
        </span>
      )}
    </p>
  );
}

export interface ReferencesProps {
  evidence: EvidenceRef[];
  citations: CitationRef[];
  webSources?: WebSourceRef[];
  onOpenReference?: (reference: EvidenceRef, page?: number) => void;
}

export function References({
  evidence,
  citations,
  webSources,
  onOpenReference,
}: ReferencesProps) {
  const [showUncited, setShowUncited] = useState(false);
  const hasEvidence = evidence.length > 0;
  const hasWebSources = (webSources?.length ?? 0) > 0;

  if (!hasEvidence && !hasWebSources) return null;

  const { cited, uncited } = partitionByCitation(evidence, citations);
  // Chip numbers index the full evidence list, so a row's number has to come
  // from there rather than from its position within a partition.
  const numberOf = (reference: EvidenceRef) => evidence.indexOf(reference) + 1;

  const citedGroups = groupByBook(cited);
  const showBookHeadings = new Set(evidence.map((entry) => entry.book_id)).size > 1;

  return (
    <section
      aria-labelledby="references-heading"
      className="space-y-2 border-t border-border pt-3"
    >
      <h4
        id="references-heading"
        className="flex items-center gap-1.5 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        <BookOpen className="size-3.5" aria-hidden />
        {cited.length} {cited.length === 1 ? "source" : "sources"}
      </h4>

      {citedGroups.map((group) => (
        <div key={group.bookId ?? group.bookTitle} className="space-y-0.5">
          <GroupHeading group={group} showBook={showBookHeadings} />
          <ul>
            {group.items.map((reference) => (
              <ReferenceRow
                key={`${reference.node_id}-${reference.rank ?? reference.pages[0]}`}
                reference={reference}
                index={numberOf(reference)}
                sharedPath={group.sharedPath}
                onOpen={onOpenReference}
              />
            ))}
          </ul>
        </div>
      ))}

      {uncited.length > 0 && (
        <Collapsible open={showUncited} onOpenChange={setShowUncited}>
          <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
            {showUncited ? (
              <ChevronDown className="size-3" aria-hidden />
            ) : (
              <ChevronRight className="size-3" aria-hidden />
            )}
            {uncited.length} more retrieved, not cited
          </CollapsibleTrigger>
          <CollapsibleContent>
            {groupByBook(uncited).map((group) => (
              <div
                key={group.bookId ?? group.bookTitle}
                className="mt-1 space-y-0.5 opacity-80"
              >
                <GroupHeading group={group} showBook={showBookHeadings} />
                <ul>
                  {group.items.map((reference) => (
                    <ReferenceRow
                      key={`${reference.node_id}-${reference.rank ?? reference.pages[0]}`}
                      reference={reference}
                      index={null}
                      sharedPath={group.sharedPath}
                      muted
                      onOpen={onOpenReference}
                    />
                  ))}
                </ul>
              </div>
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}

      {webSources && webSources.length > 0 && (
        <div className="space-y-1.5 border-t border-divider pt-2">
          <h4 className="flex items-center gap-1.5 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground">
            <Globe className="size-3.5" aria-hidden />
            {webSources.length} web {webSources.length === 1 ? "source" : "sources"}
          </h4>
          <ul className="space-y-1.5">
            {webSources.map((ws, idx) => (
              <li key={ws.url || idx} className="text-xs border-b border-divider pb-1 last:border-b-0">
                <a
                  href={ws.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-citation hover:underline flex items-center gap-1.5"
                >
                  <span className="bg-citation-muted text-citation rounded px-1.5 py-0.5 text-xs font-semibold tabular-nums shrink-0">
                    Web {ws.rank ?? idx + 1}
                  </span>
                  <span className="truncate flex-1">{ws.title}</span>
                  {ws.domain && (
                    <span className="text-muted-foreground text-xs shrink-0">
                      ({ws.domain})
                    </span>
                  )}
                </a>
                {ws.snippet && (
                  <p className="ml-6 text-muted-foreground text-xs line-clamp-2 leading-relaxed mt-0.5">
                    {ws.snippet}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
