"use client";

import {
  ArrowRight,
  Check,
  Clock3,
  FileText,
  MoreHorizontal,
  MonitorPlay,
  Trash2,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import {
  formatTimestamp,
  type VideoDetail,
  type VideoSummary,
  type VideoTimelineEntry,
} from "@/lib/video-types";
import { cn } from "@/lib/utils";

function formatLectureDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function AuthenticatedLectureFrame({
  video,
  frame,
}: {
  video: VideoSummary;
  frame: VideoTimelineEntry;
}) {
  const image = useAuthenticatedImage(
    `/api/videos/${video.video_id}/frames/${frame.frame_id}/image`,
  );

  if (image.status === "loading") {
    return <Skeleton className="aspect-[4/3] w-full rounded-lg" />;
  }

  if (image.status === "failed") {
    return (
      <div className="grid aspect-[4/3] w-full place-items-center rounded-lg border border-dashed border-border bg-background/50 text-sm text-muted-foreground">
        Lecture frame unavailable
      </div>
    );
  }

  return (
    // Frame bytes require bearer auth and therefore arrive as an object URL.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={image.url}
      alt={frame.summary ?? `Frame from ${video.title}`}
      className="aspect-[4/3] w-full rounded-lg border border-border object-cover"
    />
  );
}

function LectureFrame({
  video,
  frame,
  previewImage,
}: {
  video: VideoSummary;
  frame: VideoTimelineEntry | null;
  previewImage?: string;
}) {
  return previewImage ? (
    <Image
      src={previewImage}
      alt={`Preview frame from ${video.title}`}
      width={1152}
      height={648}
      priority
      className="aspect-[4/3] w-full rounded-lg border border-border object-cover"
    />
  ) : frame ? (
    <AuthenticatedLectureFrame video={video} frame={frame} />
  ) : (
    <Skeleton className="aspect-[4/3] w-full rounded-lg" />
  );
}

function EvidenceState({
  icon: Icon,
  label,
  state,
  detail,
  attention = false,
  connector = "none",
}: {
  icon: typeof Check;
  label: string;
  state: string;
  detail: string;
  attention?: boolean;
  connector?: "solid" | "dashed" | "none";
}) {
  return (
    <li className="relative min-w-0 border-t border-border pt-4 sm:border-0 sm:pt-0">
      {connector !== "none" ? (
        <span
          aria-hidden
          className={cn(
            "absolute left-10 right-[-0.8rem] top-[1.1rem] hidden border-t border-primary/55 sm:block",
            connector === "dashed" && "border-dashed border-seal/65",
          )}
        />
      ) : null}
      <span
        className={cn(
          "relative z-10 mb-3 grid size-9 place-items-center rounded-full border",
          attention
            ? "border-seal text-seal"
            : "border-primary/60 bg-positive-muted text-primary",
        )}
      >
        <Icon aria-hidden className="size-4" />
      </span>
      <p className="text-sm font-medium">{label}</p>
      <p className={cn("mt-0.5 text-sm", attention ? "text-seal" : "text-primary")}>
        {state}
      </p>
      <p className="mt-1 truncate font-mono text-[0.7rem] text-muted-foreground">
        {detail}
      </p>
    </li>
  );
}

export function VideoFeatureCard({
  video,
  detail,
  timeline,
  evidenceLoading,
  onDelete,
  now,
  previewImage,
}: {
  video: VideoSummary;
  detail: VideoDetail | null;
  timeline: VideoTimelineEntry[];
  evidenceLoading: boolean;
  onDelete(video: VideoSummary): void;
  now?: Date;
  previewImage?: string;
}) {
  const [confirming, setConfirming] = useState(false);
  const frame = timeline.find((entry) => entry.summary || entry.ocr_text) ?? timeline[0] ?? null;
  const slides = detail?.resources.find((resource) => resource.role === "slides");
  const screenReady = timeline.length > 0;
  const slidesReady = slides?.status === "ready";

  return (
    <article className="rounded-xl border border-border bg-card/55 p-4 sm:p-5 lg:p-6">
      <div className="grid items-center gap-6 xl:grid-cols-[minmax(0,1.06fr)_minmax(24rem,1fr)]">
        <LectureFrame video={video} frame={frame} previewImage={previewImage} />

        <div className="min-w-0">
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <h2 className="break-words font-heading text-2xl font-medium tracking-tight sm:text-3xl">
                {video.title}
              </h2>
            </div>
            {video.deletable ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="outline"
                    size="icon-sm"
                    aria-label={`Actions for ${video.title}`}
                  >
                    <MoreHorizontal aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    variant="destructive"
                    onSelect={() => setConfirming(true)}
                  >
                    <Trash2 aria-hidden />
                    Remove
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
          </div>

          <Button asChild size="lg" className="mt-5 w-full sm:w-60">
            <Link href={`/videos/${video.video_id}`}>
              Study this lecture
              <ArrowRight aria-hidden className="ml-auto" />
            </Link>
          </Button>

          <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <Clock3 aria-hidden className="size-4" />
              {formatTimestamp(video.duration_ms)}
            </span>
            <span>·</span>
            <span>{formatLectureDate(video.created_at)}</span>
          </div>

          <div className="my-6 border-t border-border" />

          <p className="mb-4 text-[0.7rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Evidence path
          </p>
          <ol className="grid gap-4 sm:grid-cols-3 sm:gap-5">
            <EvidenceState
              icon={Check}
              label="Transcript"
              state="Ready"
              detail="Grounded transcript"
              connector="solid"
            />
            <EvidenceState
              icon={MonitorPlay}
              label="Screen"
              state={evidenceLoading ? "Checking" : screenReady ? "Ready" : "Unavailable"}
              detail={screenReady ? `${timeline.length} indexed frames` : "No indexed frames"}
              attention={!evidenceLoading && !screenReady}
              connector="dashed"
            />
            <EvidenceState
              icon={FileText}
              label="Slides"
              state={evidenceLoading ? "Checking" : slidesReady ? "Ready" : "Optional"}
              detail={
                slidesReady
                  ? slides?.page_count
                    ? `${slides.page_count} pages`
                    : "Linked PDF"
                  : "Not attached"
              }
              attention={!evidenceLoading && !slidesReady}
            />
          </ol>
        </div>
      </div>

      {confirming ? (
        <div className="mt-5 flex flex-col gap-3 border-t border-destructive/40 pt-4 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-sm">
            Remove “{video.title}” and its transcript, frames, and conversations?
          </p>
          <div className="flex gap-2">
            <Button variant="destructive" size="sm" onClick={() => onDelete(video)}>
              Remove
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </article>
  );
}
