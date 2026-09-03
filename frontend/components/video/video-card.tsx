"use client";

import {
  AlertCircle,
  CircleSlash,
  Info,
  Loader2,
  MoreHorizontal,
  Play,
  RotateCcw,
  Trash2,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import {
  STAGE_COUNT,
  stageLabel,
  stagePercent,
} from "@/components/video/ingestion-status";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import { formatAdded, isUsable, videoState } from "@/lib/video-state";
import { etaWindow, progressPercent } from "@/lib/ingestion-progress";
import { formatTimestamp, type VideoSummary } from "@/lib/video-types";
import { cn } from "@/lib/utils";

export interface VideoCardProps {
  video: VideoSummary;
  onRetry(video: VideoSummary): void;
  onDelete(video: VideoSummary): void;
  /** Injected so the added-date is testable without freezing the clock. */
  now?: Date;
}

function Meta({ video, now }: { video: VideoSummary; now?: Date }) {
  const added = formatAdded(video.created_at, now);
  const parts = [
    video.source_kind === "youtube" ? "YouTube" : "Uploaded",
    video.duration_ms ? formatTimestamp(video.duration_ms) : null,
    // Three uploads of one filename are otherwise indistinguishable, which is
    // exactly what a failed re-upload leaves behind.
    added ? `added ${added}` : null,
  ].filter(Boolean);
  return (
    <p className="mt-1 truncate text-xs text-muted-foreground">
      {parts.join(" · ")}
    </p>
  );
}

/** The ⋯ menu, for a lecture that needs no decision right now. */
function CardMenu({
  video,
  onRemove,
  revealOnHover,
}: {
  video: VideoSummary;
  onRemove(): void;
  revealOnHover: boolean;
}) {
  if (!video.deletable) return null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={`Actions for ${video.title}`}
          className={cn(
            "shrink-0",
            // Quiet until wanted, but never unreachable: a card that is not a
            // link has no hover group to reveal it, so it stays visible there.
            revealOnHover &&
              "opacity-0 focus-visible:opacity-100 group-hover/card:opacity-100",
          )}
        >
          <MoreHorizontal aria-hidden />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          variant="destructive"
          onSelect={(event) => {
            event.preventDefault();
            onRemove();
          }}
        >
          <Trash2 aria-hidden />
          Remove
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The choice a stuck lecture is actually asking the reader to make. */
function Decision({
  video,
  onRetry,
  onRemove,
}: {
  video: VideoSummary;
  onRetry(): void;
  onRemove(): void;
}) {
  const retryable = Boolean(video.latest_ingestion?.retryable);
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      {retryable && (
        <Button size="xs" variant="outline" onClick={onRetry}>
          <RotateCcw aria-hidden />
          Try again
        </Button>
      )}
      {video.deletable ? (
        <Button size="xs" variant="ghost" onClick={onRemove}>
          <Trash2 aria-hidden />
          Remove
        </Button>
      ) : (
        <p className="text-xs text-muted-foreground">
          It is being processed right now. Removing it will be possible once
          this run finishes.
        </p>
      )}
    </div>
  );
}

function Confirm({
  video,
  onConfirm,
  onCancel,
}: {
  video: VideoSummary;
  onConfirm(): void;
  onCancel(): void;
}) {
  return (
    <div className="mt-3 space-y-2 rounded-md border border-destructive p-3">
      <p className="text-xs">
        Remove “{video.title}”? Its stored video file, transcript, frames, and
        conversations go with it, and this cannot be undone.
      </p>
      <div className="flex gap-2">
        <Button size="xs" variant="destructive" onClick={onConfirm}>
          Remove
        </Button>
        <Button size="xs" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function StateLine({ video }: { video: VideoSummary }) {
  const state = videoState(video);
  const job = video.latest_ingestion;

  if (state === "processing") {
    const stage = job?.stage ?? null;
    const { step, percent } = stagePercent(stage, job?.progress.percent);
    return (
      <div className="mt-3 space-y-2" role="status" aria-live="polite">
        <div className="flex items-center gap-2 text-xs">
          <Loader2 aria-hidden className="size-3.5 animate-spin" />
          <span>{stageLabel(stage)}</span>
          <span className="font-medium text-action">
            {job?.timing?.overrunning
              ? "Taking longer than usual"
              : etaWindow(job?.timing?.estimated_remaining_seconds)}
          </span>
          {step > 0 ? (
            <span className="text-muted-foreground">
              step {step} of {STAGE_COUNT}
            </span>
          ) : null}
        </div>
        <Progress value={progressPercent(job) || percent} />
      </div>
    );
  }

  if (state === "awaiting_upload") {
    return (
      <div className="mt-3 flex items-start gap-2 rounded-md bg-surface px-3 py-2 text-xs">
        <Upload aria-hidden className="mt-1 size-3.5 shrink-0" />
        <span>
          The upload never finished, so nothing has been processed. Open the
          lecture to send the file again.
        </span>
      </div>
    );
  }

  if (state === "cancelled") {
    return (
      <div className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
        <CircleSlash aria-hidden className="mt-1 size-3.5 shrink-0" />
        <span>Processing was cancelled before it finished.</span>
      </div>
    );
  }

  if (state === "failed") {
    return (
      <div className="mt-3 flex items-start gap-2 rounded-md bg-destructive-wash px-3 py-2 text-xs">
        <AlertCircle
          aria-hidden
          className="mt-1 size-3.5 shrink-0 text-destructive"
        />
        {/* The worker's own sentence, which names the cause. This showed one
            constant for every kind of failure. */}
        <span>{job?.error?.message ?? "Processing failed."}</span>
      </div>
    );
  }

  if (state === "partial") {
    return (
      <div className="mt-3 flex items-start gap-2 rounded-md bg-surface px-3 py-2">
        <Info
          aria-hidden
          className="mt-1 size-3.5 shrink-0 text-muted-foreground"
        />
        <div className="min-w-0 space-y-1 text-xs text-muted-foreground">
          {video.readiness_notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
        </div>
      </div>
    );
  }
  return null;
}

/**
 * One lecture, shown according to what can be done with it.
 *
 * A usable lecture is a link and carries no status chip: it sits under a
 * heading that already says it is ready, and a column of identical "Ready"
 * chips is noise that hides the one row saying something else. A lecture that
 * cannot be opened is deliberately not a link — following one reached a
 * workspace with nothing to answer — and states its decision inline instead.
 */
export function VideoCard({ video, onRetry, onDelete, now }: VideoCardProps) {
  const [confirming, setConfirming] = useState(false);
  const state = videoState(video);
  // A stalled upload is fixed inside the workspace, so it stays reachable.
  const openable = isUsable(state) || state === "awaiting_upload";
  const stuck = state === "failed" || state === "cancelled";

  // While a removal is being confirmed the card stops being a link. The
  // reader is answering a destructive question; a stray click should not
  // navigate away from it, and the confirmation belongs inside the card's
  // own border rather than under it.
  const asLink = openable && !confirming;

  const inner = (
    <>
      <div className="flex items-start gap-2">
        <span className="min-w-0 flex-1 truncate font-medium">{video.title}</span>
        {asLink ? (
          // Reserves the space the overlaid control occupies, so a long title
          // never runs underneath it.
          <span aria-hidden className="size-6 shrink-0" />
        ) : (
          !stuck &&
          !confirming && (
            <CardMenu
              video={video}
              onRemove={() => setConfirming(true)}
              revealOnHover={false}
            />
          )
        )}
      </div>
      <Meta video={video} now={now} />
      <StateLine video={video} />
    </>
  );

  if (!asLink) {
    return (
      <li
        className={cn(
          "rounded-lg border p-4",
          confirming ? "border-destructive" : "border-border",
        )}
      >
        {inner}
        {confirming ? (
          <Confirm
            video={video}
            onConfirm={() => onDelete(video)}
            onCancel={() => setConfirming(false)}
          />
        ) : stuck ? (
          <Decision
            video={video}
            onRetry={() => onRetry(video)}
            onRemove={() => setConfirming(true)}
          />
        ) : null}
      </li>
    );
  }

  return (
    <li className="group/card relative">
      <Link
        href={`/videos/${video.video_id}`}
        className={cn(
          "block rounded-lg border border-border p-4 transition-colors",
          "hover:bg-surface-hover",
        )}
      >
        {inner}
      </Link>
      {/* Outside the anchor: a menu nested in a link is not operable. */}
      <div className="absolute right-3 top-3 flex items-center gap-1">
        {/*
          The way into source-first study, beside the way into ask-first. A
          lecture is something to watch as well as something to ask about, and
          the library is where that choice belongs.
        */}
        {/* Visible rather than hover-revealed. The reading surface shipped
            with its entry point hidden behind a hover on a row inside a
            popover, and nobody found it; an icon that appears only under a
            pointer is not an entry point on a touch screen at all. */}
        <Button asChild size="sm" variant="outline">
          <Link href={`/watch/${video.video_id}`}>
            <Play aria-hidden />
            Watch
          </Link>
        </Button>
        <CardMenu
          video={video}
          onRemove={() => setConfirming(true)}
          revealOnHover
        />
      </div>
    </li>
  );
}
