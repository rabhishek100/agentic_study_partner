"use client";

import { BookOpenCheck, FileText, Play } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import type {
  CourseCitationRef,
  CourseEvidenceRef,
} from "@/lib/course-types";
import { formatTimestamp } from "@/lib/video-types";

const MARKER = /(\[S\d+])/g;

export function CourseAnswer({
  answer,
  evidence,
  citations,
}: {
  answer: string;
  evidence: CourseEvidenceRef[];
  citations: CourseCitationRef[];
}) {
  const byMarker = new Map(citations.map((item) => [item.marker, item]));
  const byRank = new Map(evidence.map((item) => [item.rank, item]));
  const citedLectures = [
    ...new Map(citations.map((item) => [item.video_id, item])).values(),
  ];
  return (
    <div className="space-y-5">
      <p className="whitespace-pre-wrap font-serif text-base leading-7">
        {answer.split(MARKER).map((part, index) => {
          const citation = byMarker.get(part);
          if (!citation) return <span key={`${part}-${index}`}>{part}</span>;
          const source = byRank.get(citation.evidence_rank);
          const isDocument = citation.modality === "resource_page";
          const query = isDocument
            ? new URLSearchParams({
                ...(citation.resource_id
                  ? { resource: citation.resource_id }
                  : {}),
                page: String(citation.page_number ?? 1),
              }).toString()
            : new URLSearchParams({
                t: String(Math.floor((citation.start_ms ?? 0) / 1000)),
              }).toString();
          return (
            <Button
              key={`${part}-${index}`}
              variant="ghost"
              size="sm"
              className="mx-1 h-6 gap-1 rounded-full border border-divider bg-surface px-2 align-baseline font-sans text-xs font-normal"
              asChild
            >
              <Link
                href={`/videos/${citation.video_id}?${query}`}
                title={source?.excerpt ?? "Open cited lecture evidence"}
              >
                {isDocument ? (
                  <FileText aria-hidden className="size-3" />
                ) : (
                  <Play aria-hidden className="size-3" />
                )}
                L{citation.lecture_index + 1} ·{" "}
                {isDocument
                  ? `p. ${citation.page_number ?? "?"}`
                  : formatTimestamp(citation.start_ms)}
              </Link>
            </Button>
          );
        })}
      </p>
      {citedLectures.length > 0 ? (
        <div className="flex items-center gap-2 border-t border-divider pt-3 text-xs text-muted-foreground" aria-label="Lectures cited">
          <BookOpenCheck aria-hidden className="size-4 text-action" />
          <span>Grounded in {citedLectures.length} lecture{citedLectures.length === 1 ? "" : "s"}</span>
          <span className="sr-only">
            {citedLectures.map((citation) => `Lecture ${citation.lecture_index + 1} · ${citation.video_title}`).join(", ")}
          </span>
        </div>
      ) : null}
    </div>
  );
}
