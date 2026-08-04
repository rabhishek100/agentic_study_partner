"use client";

import { ExternalLink, FileText, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";

import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  formatTimestamp,
  type VideoChapter,
  type VideoResource,
  type VideoTimelineEntry,
} from "@/lib/video-types";

const RESOURCE_STATUS: Record<VideoResource["status"], string> = {
  pending: "Waiting to be read",
  processing: "Being read",
  ready: "Ready",
  failed: "Could not be read",
};

export function ResourcePanel({
  resources,
  onOpen,
  onRebuild,
  rebuilding,
}: {
  resources: VideoResource[];
  onOpen(resource: VideoResource, page?: number): void;
  onRebuild?: () => void;
  rebuilding?: boolean;
}) {
  // A document attached after ingestion finished is not searchable until the
  // lecture is rebuilt against it, so say that plainly rather than leaving a
  // resource that quietly never gets cited.
  const unread = resources.some(
    (resource) =>
      resource.resource_kind === "pdf" && resource.status === "pending",
  );
  const rebuild =
    onRebuild && unread ? (
      <div className="mb-3 rounded-md border border-border bg-muted/40 p-2">
        <p className="mb-2 text-xs text-muted-foreground">
          A document here has not been read yet. Rebuilding indexes it without
          re-downloading the video or re-running the visual analysis.
        </p>
        <Button size="sm" variant="outline" onClick={onRebuild} disabled={rebuilding}>
          <RefreshCw
            aria-hidden
            className={rebuilding ? "animate-spin" : undefined}
          />
          {rebuilding ? "Rebuilding…" : "Rebuild with these documents"}
        </Button>
      </div>
    ) : null;

  if (resources.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No slides or links are attached to this lecture yet.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {rebuild ? <li>{rebuild}</li> : null}
      {resources.map((resource) => (
        <li key={resource.resource_id}>
          <button
            type="button"
            onClick={() => onOpen(resource)}
            className="flex w-full items-start gap-2 rounded-md border border-border p-2 text-left hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {resource.resource_kind === "pdf" ? (
              <FileText aria-hidden className="mt-0.5 size-4 shrink-0" />
            ) : (
              <ExternalLink aria-hidden className="mt-0.5 size-4 shrink-0" />
            )}
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm">{resource.title}</span>
              <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <Badge variant="outline">{resource.role}</Badge>
                {resource.page_count ? `${resource.page_count} pages` : null}
                <span
                  className={
                    resource.status === "failed" ? "text-destructive" : ""
                  }
                >
                  {RESOURCE_STATUS[resource.status]}
                </span>
              </span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function TimelineThumb({
  videoId,
  entry,
  onSeek,
}: {
  videoId: string;
  entry: VideoTimelineEntry;
  onSeek(milliseconds: number): void;
}) {
  const image = useAuthenticatedImage(
    `/api/videos/${videoId}/frames/${entry.frame_id}/image`,
  );
  return (
    <li>
      <button
        type="button"
        onClick={() => onSeek(entry.timestamp_ms)}
        className="w-full space-y-1 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        aria-label={`Play from ${formatTimestamp(entry.timestamp_ms)}`}
      >
        {image.status === "ready" ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={image.url}
            alt={entry.summary ?? "Lecture frame"}
            className="aspect-video w-full rounded-md border border-border object-cover"
          />
        ) : (
          <Skeleton className="aspect-video w-full rounded-md" />
        )}
        <span className="block text-xs font-medium">
          {formatTimestamp(entry.timestamp_ms)}
        </span>
        <span className="line-clamp-2 block text-xs text-muted-foreground">
          {entry.summary ?? entry.ocr_text ?? ""}
        </span>
      </button>
    </li>
  );
}

/**
 * A searchable strip of what was on screen. The search runs over the model's
 * description and the OCR text — the same text retrieval searches — so what
 * the reader can find by hand matches what an answer can cite.
 */
export function VisualTimeline({
  videoId,
  entries,
  onSeek,
}: {
  videoId: string;
  entries: VideoTimelineEntry[];
  onSeek(milliseconds: number): void;
}) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return entries;
    return entries.filter((entry) =>
      `${entry.summary ?? ""} ${entry.ocr_text ?? ""} ${entry.visual_types.join(" ")}`
        .toLowerCase()
        .includes(needle),
    );
  }, [entries, query]);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        The visual timeline appears once frame analysis finishes.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
        />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search what was on screen"
          aria-label="Search the visual timeline"
          className="pl-8"
        />
      </div>
      {filtered.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Nothing on screen matches “{query}”.
        </p>
      ) : (
        <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {filtered.map((entry) => (
            <TimelineThumb
              key={entry.frame_id}
              videoId={videoId}
              entry={entry}
              onSeek={onSeek}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

export function ChapterList({
  chapters,
  onSeek,
}: {
  chapters: VideoChapter[];
  onSeek(milliseconds: number): void;
}) {
  if (chapters.length === 0) return null;
  return (
    <ol className="space-y-1">
      {chapters.map((chapter) => (
        <li key={chapter.chapter_index}>
          <button
            type="button"
            onClick={() => onSeek(chapter.start_ms)}
            className="flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span className="w-12 shrink-0 text-xs text-muted-foreground">
              {formatTimestamp(chapter.start_ms)}
            </span>
            <span className="min-w-0 flex-1 truncate">{chapter.title}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}
