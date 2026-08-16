"use client";

import {
  AlertCircle,
  CheckCircle2,
  CircleSlash,
  Loader2,
  Upload,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
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
  if (readiness === "ready" || readiness === "degraded") {
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
  const { step, percent } = stagePercent(stage, ingestion?.progress.percent);

  return (
    <div className="space-y-2" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        <span>{stageLabel(stage)}</span>
        {step > 0 ? (
          <Badge variant="outline">
            step {step} of {STAGE_COUNT}
          </Badge>
        ) : null}
      </div>
      <Progress value={percent} />
      <p className="text-xs text-muted-foreground">
        Questions unlock when coverage checks pass. Processing continues if you
        leave this page.
      </p>
    </div>
  );
}
