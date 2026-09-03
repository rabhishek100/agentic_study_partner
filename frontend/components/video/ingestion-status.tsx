"use client";

import {
  AlertCircle,
  Check,
  CheckCircle2,
  Circle,
  CircleSlash,
  Loader2,
  SearchCheck,
  Upload,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  etaWindow,
  INGESTION_PHASES,
  isActiveVideoJob,
  phaseState,
  progressPercent,
  technicalDuration,
} from "@/lib/ingestion-progress";
import type { VideoIngestion, VideoReadiness } from "@/lib/video-types";

/** The pipeline, in the reader's words rather than the worker's. */
const STAGE_LABEL: Record<string, string> = {
  acquire_source: "Fetching the video",
  media_metadata: "Reading media details",
  transcript: "Getting the transcript",
  resources: "Reading linked documents",
  frame_selection: "Choosing frames",
  ocr: "Reading text on screen",
  visual_analysis: "Interpreting what is shown",
  spatial_regions: "Extracting diagrams",
  indexing: "Building the search index",
  embeddings: "Building semantic search",
  quality_gates: "Checking coverage",
  publish: "Publishing",
};

const STAGE_ORDER = Object.keys(STAGE_LABEL);
export const STAGE_COUNT = STAGE_ORDER.length;

export function stageLabel(stage: string | null): string {
  if (!stage) return "Queued for processing";
  return STAGE_LABEL[stage] ?? stage;
}

/**
 * How far along a job is, preferring what the worker reported.
 *
 * The stage index is the fallback, not the source: a stage that reports real
 * progress (frames analysed, pages read) knows more than its position does.
 */
export function stagePercent(
  stage: string | null,
  reported?: number | null,
): { step: number; percent: number } {
  const position = stage ? STAGE_ORDER.indexOf(stage) : -1;
  const step = position + 1;
  const fromStage = position >= 0 ? Math.round((step / STAGE_COUNT) * 100) : 0;
  return { step, percent: reported ?? fromStage };
}

/**
 * The lecture's state in one phrase.
 *
 * "Degraded" is reported as ready, without a parenthetical. It is a published
 * version that answers questions; which gate it missed is said in a sentence
 * beside it, where there is room to say how much it missed by. A badge reading
 * "Ready (partial)" spent the reader's attention on a worry it could not then
 * explain.
 */
export function readinessLabel(
  readiness: VideoReadiness,
  ingestion?: VideoIngestion | null,
): string {
  if (ingestion?.status === "awaiting_upload") return "Waiting for the file";
  if (ingestion?.status === "cancelled" && readiness !== "ready") {
    return "Cancelled";
  }
  return {
    processing: "Processing",
    ready: "Ready",
    degraded: "Ready",
    failed: "Could not be processed",
  }[readiness];
}

export function IngestionStatus({
  readiness,
  ingestion,
  notes = [],
}: {
  readiness: VideoReadiness;
  ingestion: VideoIngestion | null;
  /** Measured reasons a published version is short of complete. */
  notes?: string[];
}) {
  const active = isActiveVideoJob(ingestion);

  if (ingestion?.error) {
    return (
      <Alert variant="destructive">
        <AlertCircle aria-hidden />
        {/* The title used to restate the heading and the body used to restate
            the title. The cause is the only thing worth the space. */}
        <AlertTitle>{ingestion.error.message}</AlertTitle>
        {ingestion.retryable ? (
          <AlertDescription>
            This can be retried — rebuilding reuses whatever finished.
          </AlertDescription>
        ) : null}
      </Alert>
    );
  }

  if ((readiness === "ready" || readiness === "degraded") && !active) {
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <CheckCircle2 aria-hidden className="size-4 text-positive" />
          Ready to answer questions.
        </div>
        {notes.map((note) => (
          <p key={note} className="pl-6 text-xs text-muted-foreground">
            {note}
          </p>
        ))}
      </div>
    );
  }

  // A reserved upload sits at stage one with nothing happening. Reading only
  // the stage made that look identical to work in progress, so the interface
  // reported "Fetching the video" while the server was waiting on the reader.
  if (ingestion?.status === "awaiting_upload") {
    return (
      <div className="flex items-start gap-2 text-sm" role="status">
        <Upload aria-hidden className="mt-1 size-4 shrink-0" />
        <span>
          Waiting for the video file. Nothing is processing yet — the upload
          never finished, so the lecture has not started ingesting.
        </span>
      </div>
    );
  }

  if (ingestion?.status === "cancelled") {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <CircleSlash aria-hidden className="size-4" />
        Ingestion was cancelled.
      </div>
    );
  }

  const stage = ingestion?.stage ?? null;
  const { step } = stagePercent(stage, ingestion?.progress.percent);
  const percent = progressPercent(ingestion);
  const remaining = ingestion?.timing?.estimated_remaining_seconds;
  const upgrading = readiness === "ready" || readiness === "degraded";

  return (
    <section
      className="space-y-4 rounded-xl border border-primary bg-wash p-4"
      role="status"
      aria-live="polite"
      aria-labelledby="video-ingestion-title"
    >
      <div className="flex items-start gap-3">
        <span className="mt-1 grid size-8 shrink-0 place-items-center rounded-full bg-wash text-action">
          <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="video-ingestion-title" className="text-sm font-semibold">
              {upgrading
                ? "Improving this lecture’s answers"
                : "Preparing this lecture for questions"}
            </h2>
            {step > 0 ? (
              <Badge variant="outline">Step {step} of {STAGE_COUNT}</Badge>
            ) : null}
          </div>
          <p className="mt-1 text-sm text-muted-foreground">
            {ingestion?.status === "queued"
              ? `Next: ${stageLabel(stage).toLowerCase()}.`
              : stageLabel(stage)}
            {" · "}
            {ingestion?.timing?.overrunning
              ? "Taking longer than usual; completed work is saved."
              : etaWindow(remaining)}
          </p>
        </div>
      </div>

      <Progress value={percent} aria-label="Lecture ingestion progress" />

      <div className="rounded-lg border border-divider bg-surface p-3">
        <div className="flex items-start gap-2">
          <SearchCheck aria-hidden className="mt-1 size-4 shrink-0 text-action" />
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              What you’ll get
            </p>
            <p className="mt-1 text-sm leading-5">
              Ask about what was said or shown, jump to cited timestamps, and
              browse a searchable chapter and visual timeline.
            </p>
            {upgrading ? (
              <p className="mt-1 text-xs text-muted-foreground">
                The current lecture remains available while this better version is built.
              </p>
            ) : null}
          </div>
        </div>
      </div>

      <div>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Steps remaining
        </p>
        <ol className="grid gap-2 sm:grid-cols-2">
          {INGESTION_PHASES.map((phase, index) => {
            const state = phaseState(ingestion, index);
            const Icon = state === "done" ? Check : state === "active" ? Loader2 : Circle;
            return (
              <li key={phase.id} className="flex items-start gap-2 text-xs">
                <Icon
                  aria-hidden
                  className={`mt-1 size-3.5 shrink-0 ${
                    state === "done"
                      ? "text-positive"
                      : state === "active"
                        ? "animate-spin text-action motion-reduce:animate-none"
                        : "text-muted-foreground"
                  }`}
                />
                <span>
                  <span className={state === "active" ? "font-semibold" : "font-medium"}>
                    {phase.label}
                  </span>
                  <span className="block leading-5 text-muted-foreground">
                    {state === "done" ? "Done" : state === "active" ? phase.detail : "Still to come"}
                  </span>
                </span>
              </li>
            );
          })}
        </ol>
      </div>

      <p className="text-xs text-muted-foreground">
        You can close this page. Processing and checkpointed recovery continue in the background.
      </p>

      <details className="rounded-lg border border-divider bg-surface px-3 py-2 text-xs">
        <summary className="cursor-pointer font-medium text-muted-foreground hover:text-foreground">
          Technical details
        </summary>
        <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">Internal stage</dt>
          <dd className="font-mono">{stage ?? "queued"}</dd>
          <dt className="text-muted-foreground">Worker state</dt>
          <dd>{ingestion?.status ?? "queued"}</dd>
          <dt className="text-muted-foreground">Attempt</dt>
          <dd>{ingestion?.attempt ?? 0} of {ingestion?.max_attempts ?? 0}</dd>
          <dt className="text-muted-foreground">Estimated work left</dt>
          <dd>{technicalDuration(remaining)}</dd>
          {ingestion?.progress.total != null ? (
            <>
              <dt className="text-muted-foreground">Stage progress</dt>
              <dd>{ingestion.progress.completed} / {ingestion.progress.total} {ingestion.progress.unit ?? "items"}</dd>
            </>
          ) : null}
          <dt className="text-muted-foreground">Provider spend</dt>
          <dd>${Number(ingestion?.actual_cost_usd ?? 0).toFixed(2)} of ${Number(ingestion?.cost_cap_usd ?? 0).toFixed(2)} cap</dd>
          <dt className="text-muted-foreground">Recovery</dt>
          <dd>Completed stages are checkpointed; retries resume from the last valid checkpoint.</dd>
        </dl>
      </details>
    </section>
  );
}
