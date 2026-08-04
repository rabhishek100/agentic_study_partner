"use client";

import { FileText, Film, ImageIcon, Quote } from "lucide-react";
import { Fragment, useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import {
  formatTimestamp,
  type VideoCitationRef,
  type VideoEvidenceRef,
  type VideoModality,
} from "@/lib/video-types";

const MARKER = /\[S(\d+)]/g;

const MODALITY_ICON: Record<VideoModality, typeof Film> = {
  transcript: Quote,
  visual_frame: ImageIcon,
  visual_event: Film,
  resource_page: FileText,
};

export function citationLabel(
  citation: VideoCitationRef,
  evidence?: VideoEvidenceRef,
): string {
  if (citation.modality === "resource_page") {
    const document = evidence?.resource_title ?? "Document";
    return `${document} p. ${citation.page_number ?? "?"}`;
  }
  return formatTimestamp(citation.start_ms);
}

interface VideoAnswerProps {
  answer: string;
  evidence: VideoEvidenceRef[];
  citations: VideoCitationRef[];
  onSeek(milliseconds: number): void;
  onOpenDocument(citation: VideoCitationRef): void;
}

/**
 * Render the answer with its markers turned into controls.
 *
 * A timestamp marker seeks the player; a page marker opens that page. The two
 * are never merged into one "source" chip, because the lecture and the slides
 * are different places and the answer is only allowed to claim what it cited.
 */
export function VideoAnswer({
  answer,
  evidence,
  citations,
  onSeek,
  onOpenDocument,
}: VideoAnswerProps) {
  const byRank = useMemo(
    () => new Map(evidence.map((item) => [item.rank, item])),
    [evidence],
  );
  const citationByMarker = useMemo(
    () => new Map(citations.map((item) => [item.marker, item])),
    [citations],
  );

  const segments = useMemo(() => {
    const parts: { text: string; marker?: string }[] = [];
    let index = 0;
    for (const match of answer.matchAll(MARKER)) {
      const start = match.index ?? 0;
      if (start > index) parts.push({ text: answer.slice(index, start) });
      parts.push({ text: match[0], marker: match[0] });
      index = start + match[0].length;
    }
    if (index < answer.length) parts.push({ text: answer.slice(index) });
    return parts;
  }, [answer]);

  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed">
      {segments.map((segment, position) => {
        const citation = segment.marker
          ? citationByMarker.get(segment.marker)
          : undefined;
        if (!segment.marker || !citation) {
          return <Fragment key={position}>{segment.text}</Fragment>;
        }
        const item = byRank.get(citation.evidence_rank);
        const Icon = MODALITY_ICON[citation.modality];
        const label = citationLabel(citation, item);
        return (
          <Tooltip key={position}>
            <TooltipTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className={cn(
                  "mx-0.5 h-6 gap-1 rounded-full border border-border bg-muted/60 px-2",
                  "align-baseline text-xs font-normal",
                )}
                onClick={() =>
                  citation.modality === "resource_page"
                    ? onOpenDocument(citation)
                    : onSeek(citation.start_ms ?? 0)
                }
              >
                <Icon aria-hidden className="size-3" />
                {label}
              </Button>
            </TooltipTrigger>
            <TooltipContent className="max-w-sm">
              <span className="line-clamp-4 text-xs">
                {item?.excerpt ?? "Cited evidence"}
              </span>
            </TooltipContent>
          </Tooltip>
        );
      })}
    </p>
  );
}
