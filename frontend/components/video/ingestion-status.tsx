"use client";

import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";

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

export function readinessLabel(readiness: VideoReadiness): string {
  return {
    processing: "Processing",
    ready: "Ready",
    degraded: "Ready (partial)",
    failed: "Failed",
  }[readiness];
}

export function IngestionStatus({
  readiness,
  ingestion,
}: {
  readiness: VideoReadiness;
  ingestion: VideoIngestion | null;
}) {
  if (readiness === "ready" || readiness === "degraded") {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <CheckCircle2 aria-hidden className="size-4 text-positive" />
        {readiness === "degraded"
          ? "Ready, with some evidence missing — answers say so when it matters."
          : "Ready to answer questions."}
      </div>
    );
  }

  if (ingestion?.error) {
    return (
      <Alert variant="destructive">
        <AlertCircle aria-hidden />
        <AlertTitle>Ingestion failed</AlertTitle>
        <AlertDescription>
          {ingestion.error.message}
          {ingestion.retryable ? " You can retry it." : null}
        </AlertDescription>
      </Alert>
    );
  }

  const stage = ingestion?.stage ?? null;
  const position = stage ? STAGE_ORDER.indexOf(stage) : -1;
  const percent =
    ingestion?.progress.percent ??
    (position >= 0 ? Math.round(((position + 1) / STAGE_ORDER.length) * 100) : 0);

  return (
    <div className="space-y-2" role="status" aria-live="polite">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        <span>
          {stage ? (STAGE_LABEL[stage] ?? stage) : "Queued for processing"}
        </span>
        {position >= 0 ? (
          <Badge variant="outline">
            step {position + 1} of {STAGE_ORDER.length}
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
