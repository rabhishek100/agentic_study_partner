"use client";

import { ListVideo, Play } from "lucide-react";
import { useState } from "react";

import { monogram } from "@/components/video/video-poster";
import { cn } from "@/lib/utils";

export function courseThumbnailUrl(youtubeVideoId: string | null): string | null {
  return youtubeVideoId
    ? `https://i.ytimg.com/vi/${youtubeVideoId}/mqdefault.jpg`
    : null;
}

/** A lightweight playlist face built from its first available lecture. */
export function CoursePoster({
  title,
  youtubeVideoId,
  lectureCount,
  className,
  eager = false,
}: {
  title: string;
  youtubeVideoId: string | null;
  lectureCount: number;
  className?: string;
  eager?: boolean;
}) {
  const [failed, setFailed] = useState(false);
  const source = courseThumbnailUrl(youtubeVideoId);

  return (
    <div
      className={cn(
        "relative aspect-video overflow-hidden rounded-lg border border-divider bg-surface",
        className,
      )}
    >
      <span
        aria-hidden
        className="grid size-full place-items-center font-serif text-3xl font-medium text-muted-foreground"
      >
        {monogram(title)}
      </span>
      {source && !failed ? (
        <img
          src={source}
          alt=""
          loading={eager ? "eager" : "lazy"}
          decoding="async"
          className="absolute inset-0 size-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : null}
      <span className="absolute inset-0 grid place-items-center">
        <span className="grid size-10 place-items-center rounded-full bg-card text-foreground shadow-sm">
          <Play aria-hidden className="size-4 fill-current" />
        </span>
      </span>
      <span className="absolute bottom-2 right-2 flex items-center gap-1 rounded-sm bg-card px-2 py-1 text-xs font-medium text-foreground shadow-sm">
        <ListVideo aria-hidden className="size-3.5" />
        {lectureCount} lecture{lectureCount === 1 ? "" : "s"}
      </span>
    </div>
  );
}
