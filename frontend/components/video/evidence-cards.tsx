"use client";

import { ChevronDown } from "lucide-react";

import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatTimestamp,
  type VideoEvidenceRef,
  type VideoTurnResult,
  type VideoVisualCard,
} from "@/lib/video-types";

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

function EvidenceRow({
  item,
  onSeek,
}: {
  item: VideoEvidenceRef;
  onSeek(milliseconds: number): void;
}) {
  const locator =
    item.modality === "resource_page"
      ? `${item.resource_title ?? "Document"} p. ${item.page_number}`
      : formatTimestamp(item.start_ms);
  return (
    <li className="space-y-1 border-b border-border/60 py-2 last:border-0">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge variant="outline">S{item.rank}</Badge>
        <span className="text-muted-foreground">{item.modality}</span>
        {item.start_ms === null ? (
          <span className="font-medium">{locator}</span>
        ) : (
          <button
            type="button"
            className="font-medium underline-offset-2 hover:underline"
            onClick={() => onSeek(item.start_ms ?? 0)}
          >
            {locator}
          </button>
        )}
        <span className="text-muted-foreground">
          {item.retrieval_method} · {item.score.toFixed(3)}
        </span>
      </div>
      <p className="line-clamp-3 text-xs text-muted-foreground">
        {item.excerpt}
      </p>
    </li>
  );
}

/**
 * Everything the answer rested on but did not show: the rest of the evidence,
 * how it was retrieved, what it scored, what the turn cost, and which trace
 * it belongs to.
 */
export function TurnDiagnostics({
  result,
  onSeek,
}: {
  result: VideoTurnResult;
  onSeek(milliseconds: number): void;
}) {
  return (
    <Collapsible>
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1 text-xs">
          <ChevronDown aria-hidden className="size-3" />
          Evidence and diagnostics ({result.evidence.length})
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1 rounded-md border border-border bg-card/60 p-3">
        <dl className="mb-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground sm:grid-cols-4">
          <div>
            <dt className="inline">Retrieval passes: </dt>
            <dd className="inline text-foreground">
              {result.retrieval_attempts}
            </dd>
          </div>
          <div>
            <dt className="inline">Cost: </dt>
            <dd className="inline text-foreground">
              ${result.cost_usd.toFixed(4)}
            </dd>
          </div>
          <div>
            <dt className="inline">Outcome: </dt>
            <dd className="inline text-foreground">{result.outcome}</dd>
          </div>
          <div>
            <dt className="inline">Trace: </dt>
            <dd className="inline text-foreground">
              {result.trace_id ? result.trace_id.slice(0, 8) : "not traced"}
            </dd>
          </div>
        </dl>
        {result.standalone_query &&
        result.standalone_query !== result.question ? (
          <p className="mb-2 text-xs text-muted-foreground">
            Searched as: “{result.standalone_query}”
          </p>
        ) : null}
        {result.sufficiency_reason ? (
          <p className="mb-2 text-xs text-muted-foreground">
            {result.sufficiency_reason}
          </p>
        ) : null}
        <ul>
          {result.evidence.map((item) => (
            <EvidenceRow key={item.evidence_id} item={item} onSeek={onSeek} />
          ))}
        </ul>
      </CollapsibleContent>
    </Collapsible>
  );
}
