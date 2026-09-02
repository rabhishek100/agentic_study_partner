"use client";

import { Loader2, SearchCheck } from "lucide-react";
import Link from "next/link";

import {
  STAGE_COUNT,
  stageLabel,
  stagePercent,
} from "@/components/video/ingestion-status";
import { Progress } from "@/components/ui/progress";
import {
  etaWindow,
  progressPercent,
  technicalDuration,
} from "@/lib/ingestion-progress";
import type { VideoSummary } from "@/lib/video-types";

/**
 * Everything currently being read, in one place.
 *
 * The stage detail was already good and already honest; it was just kept
 * inside whichever card happened to be processing, so a reader waiting twenty
 * minutes had to find it, and the page never answered the question they
 * actually have — may I close this. The band answers it once, above the
 * library, and states the count so several runs do not read as several
 * unrelated events.
 */
export function ProcessingBand({ videos }: { videos: VideoSummary[] }) {
  if (videos.length === 0) return null;
  const estimates = videos.map(
    (video) => video.latest_ingestion?.timing?.estimated_remaining_seconds,
  );
  const remaining = estimates.some((value) => value == null)
    ? null
    : estimates.reduce<number>((sum, value) => sum + (value ?? 0), 0);

  return (
    <section
      aria-labelledby="processing-band"
      className="flex flex-col gap-4 rounded-xl border border-primary bg-wash p-4"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="processing-band" className="text-sm font-semibold">
            Processing {videos.length} lecture{videos.length === 1 ? "" : "s"}
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            This keeps running if you close the page. Completed stages are saved.
          </p>
        </div>
        <p className="text-sm font-semibold text-action">{etaWindow(remaining)}</p>
      </div>

      <div className="flex items-start gap-2 rounded-lg border border-divider bg-surface p-3">
        <SearchCheck aria-hidden className="mt-1 size-4 shrink-0 text-action" />
        <p className="text-xs leading-5">
          When finished, each lecture can answer from its transcript, slides,
          diagrams, and exact timestamps—with chapters and a visual timeline.
        </p>
      </div>

      <ul className="flex flex-col gap-3">
        {videos.map((video) => {
          const job = video.latest_ingestion;
          const { step, percent } = stagePercent(
            job?.stage ?? null,
            job?.progress.percent,
          );
          return (
            <li key={video.video_id} className="flex flex-col gap-2">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <Loader2
                  aria-hidden
                  className="size-3.5 shrink-0 animate-spin text-action motion-reduce:animate-none"
                />
                <Link
                  href={`/videos/${video.video_id}`}
                  className="min-w-0 truncate text-xs font-medium hover:underline"
                >
                  {video.title}
                </Link>
                <span className="text-xs text-muted-foreground">
                  {stageLabel(job?.stage ?? null)}
                </span>
                <span className="text-xs font-medium text-action">
                  {job?.timing?.overrunning
                    ? "Taking longer than usual"
                    : etaWindow(job?.timing?.estimated_remaining_seconds)}
                </span>
                {step > 0 ? (
                  <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
                    step {step} of {STAGE_COUNT}
                  </span>
                ) : null}
              </div>
              <Progress value={progressPercent(job) || percent} />
            </li>
          );
        })}
      </ul>

      <details className="rounded-lg border border-divider bg-surface px-3 py-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer font-medium hover:text-foreground">
          Technical details
        </summary>
        <div className="mt-3 space-y-2">
          <p>
            Estimated worker time left: <span className="text-foreground">{technicalDuration(remaining)}</span>.
            The ETA adds remaining stage estimates for the production worker and widens the result for provider variance.
          </p>
          <p>
            Every stage writes a durable checkpoint. A retry reuses completed
            transcript, frame, visual-analysis, and index work instead of starting over.
          </p>
        </div>
      </details>
    </section>
  );
}
