"use client";

import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import { Skeleton } from "@/components/ui/skeleton";
import { formatTimestamp, type VideoVisualCard } from "@/lib/video-types";

function FrameThumbnail({
  videoId,
  frameId,
  alt,
}: {
  videoId: string;
  frameId: number;
  alt: string;
}) {
  const image = useAuthenticatedImage(
    `/api/videos/${videoId}/frames/${frameId}/image`,
  );
  if (image.status === "loading") {
    return <Skeleton className="aspect-video w-full rounded-md" />;
  }
  if (image.status === "failed") {
    return (
      <div className="grid aspect-video w-full place-items-center rounded-md border border-dashed border-border text-xs text-muted-foreground">
        Frame unavailable
      </div>
    );
  }
  return (
    // The endpoint needs a bearer token, so the bytes arrive as an object URL
    // rather than a plain src the optimizer could handle.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={image.url}
      alt={alt}
      className="aspect-video w-full rounded-md border border-border object-cover"
    />
  );
}

export function VisualEvidence({
  videoId,
  cards,
  onSeek,
}: {
  videoId: string;
  cards: VideoVisualCard[];
  onSeek(milliseconds: number): void;
}) {
  if (cards.length === 0) return null;
  return (
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {cards.map((card) => (
        <li key={`${card.evidence_rank}-${card.frame_id ?? "event"}`}>
          <button
            type="button"
            onClick={() => onSeek(card.start_ms)}
            className="group w-full space-y-1 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            aria-label={`Play from ${formatTimestamp(card.start_ms)}: ${card.summary}`}
          >
            {card.frame_id ? (
              <FrameThumbnail
                videoId={videoId}
                frameId={card.frame_id}
                alt={card.summary}
              />
            ) : (
              <div className="grid aspect-video w-full place-items-center rounded-md border border-border bg-muted/50 text-xs text-muted-foreground">
                Change
              </div>
            )}
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">
                {formatTimestamp(card.start_ms)}
              </span>
              {card.kind === "transition" ? "before → after" : null}
            </span>
            <span className="line-clamp-2 text-xs text-muted-foreground">
              {card.summary}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

