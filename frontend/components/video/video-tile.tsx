"use client";

import { Info, MoreHorizontal, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { VideoPoster } from "@/components/video/video-poster";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatAdded, videoState } from "@/lib/video-state";
import type { VideoSummary } from "@/lib/video-types";

export interface VideoTileProps {
  video: VideoSummary;
  onDelete(video: VideoSummary): void;
  /** Injected so the added-date is testable without freezing the clock. */
  now?: Date;
}

/**
 * One lecture in the library grid.
 *
 * The whole tile is the link, and it carries no "Ready" chip — it sits in a
 * region that already says these are ready, and a grid of identical badges
 * hides the one tile saying something else. What it does carry is the poster,
 * because a library of filenames cannot be scanned at all.
 *
 * `VideoCard` still owns the lectures that need a decision. This is
 * deliberately the simpler component: a tile that is only ever a link never
 * has to stop being one mid-interaction.
 */
export function VideoTile({ video, onDelete, now }: VideoTileProps) {
  const [confirming, setConfirming] = useState(false);
  const added = formatAdded(video.created_at, now);
  const partial = videoState(video) === "partial";

  if (confirming) {
    return (
      <li className="flex flex-col gap-3 rounded-lg border border-destructive p-4">
        <p className="text-xs leading-5">
          Remove “{video.title}”? Its stored video file, transcript, frames, and
          conversations go with it, and this cannot be undone.
        </p>
        <div className="flex gap-2">
          <Button size="xs" variant="destructive" onClick={() => onDelete(video)}>
            Remove
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </div>
      </li>
    );
  }

  return (
    <li className="group/tile relative flex flex-col">
      <Link
        href={`/videos/${video.video_id}`}
        className="flex flex-col gap-2 rounded-lg p-1 transition-colors hover:bg-surface-hover"
      >
        <VideoPoster video={video} />
        <div className="flex flex-col gap-1 px-1 pb-1">
          {/* Two lines, then ellipsis: lecture titles are long and a tile that
              grows to fit one breaks the grid's rhythm for every other tile. */}
          <span className="line-clamp-2 text-sm font-medium leading-snug">
            {video.title}
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {[
              video.source_kind === "youtube" ? "YouTube" : "Uploaded",
              added ? `added ${added}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
          {partial ? (
            /*
              Measured, not a badge. "Partial" alone is a worry with no
              content; the note says what the published version is missing and
              by how much, which is what decides whether to re-run it.
            */
            <span className="flex items-start gap-2 text-xs text-muted-foreground">
              <Info aria-hidden className="mt-1 size-3.5 shrink-0" />
              <span className="line-clamp-2">{video.readiness_notes[0]}</span>
            </span>
          ) : null}
        </div>
      </Link>

      {video.deletable ? (
        /* Outside the anchor: a menu nested in a link is not operable. */
        <div className="absolute right-2 top-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                size="icon-xs"
                variant="secondary"
                aria-label={`Actions for ${video.title}`}
                className="opacity-0 focus-visible:opacity-100 group-hover/tile:opacity-100"
              >
                <MoreHorizontal aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                variant="destructive"
                onSelect={(event) => {
                  event.preventDefault();
                  setConfirming(true);
                }}
              >
                <Trash2 aria-hidden />
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ) : null}
    </li>
  );
}
