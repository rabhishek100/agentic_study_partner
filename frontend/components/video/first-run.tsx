"use client";

import { FileVideo, Link2 } from "lucide-react";

import { AddVideoDialog } from "@/components/video/add-video-dialog";
import { STAGE_COUNT } from "@/components/video/ingestion-status";

/**
 * The screen someone sees before they have committed anything.
 *
 * It replaces a dashed box reading "No videos yet", which is a statement of
 * the obvious placed where the most consequential decision on the page is
 * made. Adding a lecture costs a long wait and real model spend, so the empty
 * state's job is to say what the two ways in are and what happens after —
 * enough that the wait is expected rather than discovered.
 *
 * The stages are named and the count is real, but no duration is promised.
 * Every finished lecture records `created_at` and `ready_at`, so the honest
 * estimate is the median of this reader's own runs; until that is computed,
 * saying nothing beats saying a number somebody guessed.
 */
export function FirstRun({ onAdded }: { onAdded(): void }) {
  return (
    <section
      aria-labelledby="first-run-heading"
      className="flex flex-col gap-6 rounded-lg border border-divider p-6 sm:p-8"
    >
      <div className="flex flex-col gap-2">
        <h2 id="first-run-heading" className="font-serif text-lg font-semibold">
          Add your first lecture
        </h2>
        <p className="max-w-prose text-sm leading-6 text-muted-foreground">
          A lecture becomes askable: you get answers grounded in what was said,
          what was on screen, and any slides you attach — each one citing the
          moment it came from.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-2 rounded-lg border border-divider bg-surface p-4">
          <span className="flex items-center gap-2 text-sm font-medium">
            <Link2 aria-hidden className="size-4 shrink-0 text-action" />
            Paste a YouTube link
          </span>
          <p className="text-xs leading-5 text-muted-foreground">
            The quickest way in. Captions and the published chapter list come
            with it, so nothing has to be transcribed from audio.
          </p>
        </div>
        <div className="flex flex-col gap-2 rounded-lg border border-divider bg-surface p-4">
          <span className="flex items-center gap-2 text-sm font-medium">
            <FileVideo aria-hidden className="size-4 shrink-0 text-action" />
            Upload a recording
          </span>
          <p className="text-xs leading-5 text-muted-foreground">
            MP4, WebM, or MOV, up to 2&nbsp;GB. Attach a .vtt if you have one:
            without captions the audio goes to a paid model, which on a long
            recording can reach the per-lecture cost cap.
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-divider pt-4">
        <p className="text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground">
          Then, without you
        </p>
        <ol className="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground">
          <li>1. Fetch the video</li>
          <li>2. Get the transcript</li>
          <li>3. Read what is on screen</li>
          <li>4. Index and publish</li>
        </ol>
        <p className="max-w-prose text-xs leading-5 text-muted-foreground">
          {STAGE_COUNT} stages in all, and they run in the background — you can
          close the page. The lecture appears here as it goes, and answers
          unlock when it finishes.
        </p>
      </div>

      <div className="w-full sm:w-56">
        <AddVideoDialog onAdded={onAdded} />
      </div>
    </section>
  );
}
