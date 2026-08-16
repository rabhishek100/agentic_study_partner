"use client";

import {
  ChevronDown,
  ChevronRight,
  FileText,
  Film,
  ImageIcon,
  Layers,
  Quote,
} from "lucide-react";
import { useState } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  groupBySource,
  partitionByCitation,
  type VideoReferenceGroup,
} from "@/lib/video-references";
import {
  formatTimestamp,
  type VideoCitationRef,
  type VideoDocumentTarget,
  type VideoEvidenceRef,
  type VideoModality,
} from "@/lib/video-types";
import { cn } from "@/lib/utils";

const MODALITY_ICON: Record<VideoModality, typeof Film> = {
  transcript: Quote,
  visual_frame: ImageIcon,
  visual_event: Film,
  resource_page: FileText,
};

const MODALITY_LABEL: Record<VideoModality, string> = {
  transcript: "Said",
  visual_frame: "On screen",
  visual_event: "Screen changed",
  resource_page: "Page",
};

/** Where this evidence lives, phrased as the place rather than the table. */
function locator(reference: VideoEvidenceRef): string {
  if (reference.modality === "resource_page") {
    return `p. ${reference.page_number ?? "?"}`;
  }
  const start = formatTimestamp(reference.start_ms);
  if (
    reference.modality === "transcript" &&
    reference.end_ms !== null &&
    reference.end_ms !== reference.start_ms
  ) {
    return `${start}–${formatTimestamp(reference.end_ms)}`;
  }
  return start;
}

function ReferenceRow({
  reference,
  index,
  muted,
  onSeek,
  onOpenDocument,
}: {
  reference: VideoEvidenceRef;
  index: number | null;
  muted?: boolean;
  onSeek(milliseconds: number): void;
  onOpenDocument(target: VideoDocumentTarget): void;
}) {
  const [open, setOpen] = useState(false);
  const Icon = MODALITY_ICON[reference.modality];
  const isDocument = reference.modality === "resource_page";
  // A timestamp always seeks. A page can only be opened when the document it
  // belongs to is still attached; a detached one keeps its citation but has
  // nothing left to open.
  const canOpen = isDocument ? Boolean(reference.resource_id) : true;

  return (
    <li className="border-b border-divider last:border-b-0">
      <div className="flex items-baseline gap-2 py-2">
        <span
          className={cn(
            "mt-1 flex size-4 shrink-0 items-center justify-center rounded text-xs font-semibold tabular-nums",
            index === null
              ? "text-muted-foreground"
              : "bg-citation-muted text-citation",
          )}
        >
          {index ?? "·"}
        </span>

        <p className="min-w-0 flex-1 text-sm leading-snug">
          <Icon
            aria-hidden
            className="mr-2 inline size-3.5 align-[-0.15em] text-muted-foreground"
          />
          <span className="text-muted-foreground">
            {MODALITY_LABEL[reference.modality]}
          </span>
          <span className={cn("ml-2", muted ? "text-muted-foreground" : "font-medium")}>
            {locator(reference)}
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
          {canOpen && (
            <button
              type="button"
              onClick={() =>
                isDocument && reference.resource_id
                  ? onOpenDocument({
                      resourceId: reference.resource_id,
                      page: reference.page_number ?? 1,
                      excerpt: reference.excerpt,
                    })
                  : onSeek(reference.start_ms ?? 0)
              }
              className="text-xs text-muted-foreground transition-colors hover:text-citation"
            >
              {isDocument ? "Open" : "Play"}
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

function GroupHeading({ group }: { group: VideoReferenceGroup }) {
  return (
    <p className="flex items-baseline gap-2 text-xs">
      <span className="font-medium text-foreground">{group.title}</span>
      <span className="text-muted-foreground">
        {group.items.length}{" "}
        {group.items.length === 1 ? "passage" : "passages"}
      </span>
    </p>
  );
}

export interface VideoReferencesProps {
  evidence: VideoEvidenceRef[];
  citations: VideoCitationRef[];
  onSeek(milliseconds: number): void;
  onOpenDocument(target: VideoDocumentTarget): void;
}

/**
 * What the answer rested on, grouped by the source it came from.
 *
 * Deliberately the book chat's reference list rather than a second design:
 * cited first with the marker numbers the prose uses, uncited retrieval
 * demoted behind a disclosure, and each row openable at the exact moment or
 * page it names.
 */
export function VideoReferences({
  evidence,
  citations,
  onSeek,
  onOpenDocument,
}: VideoReferencesProps) {
  const [showUncited, setShowUncited] = useState(false);
  if (evidence.length === 0) return null;

  const { cited, uncited } = partitionByCitation(evidence, citations);
  // Marker numbers index the full evidence list, so a row's number comes from
  // there rather than from its position within a partition.
  const numberOf = (reference: VideoEvidenceRef) => reference.rank;

  return (
    <section
      aria-labelledby="video-references-heading"
      className="space-y-2 border-t border-border pt-3"
    >
      <h4
        id="video-references-heading"
        className="flex items-center gap-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        <Layers className="size-3.5" aria-hidden />
        {cited.length} {cited.length === 1 ? "source" : "sources"}
      </h4>

      {groupBySource(cited).map((group) => (
        <div key={group.key} className="space-y-1">
          <GroupHeading group={group} />
          <ul>
            {group.items.map((reference) => (
              <ReferenceRow
                key={reference.evidence_id}
                reference={reference}
                index={numberOf(reference)}
                onSeek={onSeek}
                onOpenDocument={onOpenDocument}
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
            {groupBySource(uncited).map((group) => (
              <div key={group.key} className="mt-1 space-y-1 opacity-80">
                <GroupHeading group={group} />
                <ul>
                  {group.items.map((reference) => (
                    <ReferenceRow
                      key={reference.evidence_id}
                      reference={reference}
                      index={null}
                      muted
                      onSeek={onSeek}
                      onOpenDocument={onOpenDocument}
                    />
                  ))}
                </ul>
              </div>
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}
    </section>
  );
}
