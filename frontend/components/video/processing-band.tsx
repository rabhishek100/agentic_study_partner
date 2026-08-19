"use client";

import { Loader2 } from "lucide-react";

import {
  STAGE_COUNT,
  stageLabel,
  stagePercent,
} from "@/components/video/ingestion-status";
import { Progress } from "@/components/ui/progress";
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

  return (
    <section
      aria-labelledby="processing-band"
      className="flex flex-col gap-3 rounded-lg border border-divider bg-surface p-4"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <h2 id="processing-band" className="text-sm font-medium">
          Processing {videos.length} lecture{videos.length === 1 ? "" : "s"}
        </h2>
        <p className="text-xs text-muted-foreground">
          This keeps running if you close the page. Questions unlock when it
          finishes.
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
                <span className="min-w-0 truncate text-xs font-medium">
                  {video.title}
                </span>
                <span className="text-xs text-muted-foreground">
                  {stageLabel(job?.stage ?? null)}
                </span>
                {step > 0 ? (
                  <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
                    step {step} of {STAGE_COUNT}
                  </span>
                ) : null}
              </div>
              <Progress value={percent} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}
