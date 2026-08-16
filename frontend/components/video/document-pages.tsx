"use client";

import { FileText, ImageOff } from "lucide-react";

import { Skeleton } from "@/components/ui/skeleton";
import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import type {
  VideoCitationRef,
  VideoDocumentTarget,
  VideoEvidenceRef,
} from "@/lib/video-types";
import { cn } from "@/lib/utils";

const MAXIMUM_PAGES = 4;

export function pageImageSource(
  videoId: string,
  resourceId: string,
  page: number,
): string {
  return `/api/videos/${videoId}/resources/${resourceId}/pages/${page}/image`;
}

/**
 * The document pages this answer cited, deduplicated and in citation order.
 *
 * One page can be cited by several markers, and a marker can point at evidence
 * from a page already shown; either way the page belongs on screen once.
 */
export function citedPages(
  evidence: VideoEvidenceRef[],
  citations: VideoCitationRef[],
): VideoEvidenceRef[] {
  const cited = new Set(citations.map((citation) => citation.evidence_rank));
  const seen = new Set<string>();
  const pages: VideoEvidenceRef[] = [];
  for (const item of evidence) {
    if (item.modality !== "resource_page") continue;
    if (!cited.has(item.rank)) continue;
    if (!item.resource_id || item.page_number === null) continue;
    const key = `${item.resource_id}:${item.page_number}`;
    if (seen.has(key)) continue;
    seen.add(key);
    pages.push(item);
  }
  return pages.slice(0, MAXIMUM_PAGES);
}

function PageImage({
  videoId,
  reference,
  className,
}: {
  videoId: string;
  reference: VideoEvidenceRef;
  className?: string;
}) {
  const image = useAuthenticatedImage(
    pageImageSource(videoId, reference.resource_id!, reference.page_number!),
  );

  if (image.status === "loading") {
    return <Skeleton className={cn("aspect-4/3 w-full", className)} />;
  }
  if (image.status === "failed") {
    return (
      <span className="flex aspect-4/3 items-center justify-center gap-2 px-3 text-xs text-muted-foreground">
        <ImageOff className="size-4 shrink-0" aria-hidden />
        Page {reference.page_number} could not be shown.
      </span>
    );
  }
  return (
    // The endpoint needs a bearer token, so the bytes arrive as an object URL
    // rather than a plain src the optimizer could handle.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={image.url}
      alt={`${reference.resource_title ?? "Document"}, page ${reference.page_number}`}
      className={cn("w-full bg-background object-contain", className)}
    />
  );
}

export interface DocumentPagesProps {
  videoId: string;
  evidence: VideoEvidenceRef[];
  citations: VideoCitationRef[];
  onOpen(target: VideoDocumentTarget): void;
}

/**
 * The cited slide, shown rather than described.
 *
 * A book cites a figure inside a page of prose, so its figures are extracted
 * and captioned at ingest. A slide is usually the figure, so the page itself
 * is what belongs beside the answer — and a diagram's meaning is in the
 * picture, which a page number alone does not carry. Clicking one opens the
 * viewer there, where the passage is highlighted and the rest of the deck is
 * reachable.
 */
export function DocumentPages({
  videoId,
  evidence,
  citations,
  onOpen,
}: DocumentPagesProps) {
  const pages = citedPages(evidence, citations);
  if (pages.length === 0) return null;

  return (
    <section aria-labelledby="cited-pages-heading" className="space-y-2">
      <h4
        id="cited-pages-heading"
        className="flex items-center gap-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        <FileText className="size-3.5" aria-hidden />
        {pages.length === 1 ? "Cited page" : "Cited pages"}
      </h4>

      <ul
        className={cn(
          "grid gap-2",
          pages.length === 1 ? "grid-cols-1" : "grid-cols-2",
        )}
      >
        {pages.map((reference) => (
          <li key={`${reference.resource_id}:${reference.page_number}`}>
            <button
              type="button"
              onClick={() =>
                onOpen({
                  resourceId: reference.resource_id!,
                  page: reference.page_number!,
                  excerpt: reference.excerpt,
                })
              }
              className="block w-full overflow-hidden rounded-lg border border-border bg-card text-left transition-colors hover:border-citation"
            >
              <PageImage
                videoId={videoId}
                reference={reference}
                className="max-h-64"
              />
              <span className="block border-t border-border px-3 py-2 text-xs text-muted-foreground">
                <span className="block truncate">
                  {reference.resource_title ?? "Linked document"}
                </span>
                <span className="block">p. {reference.page_number}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
