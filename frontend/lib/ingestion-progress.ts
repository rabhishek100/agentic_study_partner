import type { VideoIngestion } from "@/lib/video-types";

export const ACTIVE_VIDEO_JOB_STATUSES = new Set<VideoIngestion["status"]>([
  "queued",
  "running",
  "retry_scheduled",
]);

export const INGESTION_PHASES = [
  {
    id: "content",
    label: "Capture what is taught",
    detail: "Get the transcript, video details, and linked material.",
    stages: ["acquire_source", "media_metadata", "transcript", "resources"],
  },
  {
    id: "visuals",
    label: "Understand what is shown",
    detail: "Read slide text, diagrams, equations, and important frames.",
    stages: ["frame_selection", "ocr", "visual_analysis", "spatial_regions"],
  },
  {
    id: "search",
    label: "Make it searchable",
    detail: "Build keyword and meaning-based search across the lecture.",
    stages: ["indexing", "embeddings"],
  },
  {
    id: "publish",
    label: "Check and publish",
    detail: "Verify coverage, then make the new evidence available for answers.",
    stages: ["quality_gates", "publish"],
  },
] as const;

const STAGE_ORDER: string[] = INGESTION_PHASES.flatMap((phase) => [
  ...phase.stages,
]);

export type IngestionPhaseState = "done" | "active" | "pending";

export function isActiveVideoJob(job: VideoIngestion | null | undefined) {
  return Boolean(job && ACTIVE_VIDEO_JOB_STATUSES.has(job.status));
}

export function phaseState(
  job: VideoIngestion | null | undefined,
  phaseIndex: number,
): IngestionPhaseState {
  if (job?.status === "ready") return "done";
  const stageIndex = job?.stage ? STAGE_ORDER.indexOf(job.stage) : -1;
  const phase = INGESTION_PHASES[phaseIndex]!;
  const first = STAGE_ORDER.indexOf(phase.stages[0]!);
  const last = STAGE_ORDER.indexOf(phase.stages[phase.stages.length - 1]!);
  if (stageIndex > last) return "done";
  if (stageIndex >= first && stageIndex <= last) return "active";
  return "pending";
}

export function progressPercent(job: VideoIngestion | null | undefined) {
  if (!job) return 0;
  if (job.status === "ready") return 100;
  if (job.timing) return job.timing.percent;
  if (job.progress.percent != null) return job.progress.percent;
  const stageIndex = job.stage ? STAGE_ORDER.indexOf(job.stage) : -1;
  return stageIndex < 0 ? 0 : Math.round(((stageIndex + 1) / STAGE_ORDER.length) * 100);
}

function roundedMinutes(seconds: number, direction: "down" | "up") {
  const raw = Math.max(1, seconds / 60);
  const quantum = raw < 10 ? 1 : raw < 30 ? 5 : raw < 120 ? 15 : 30;
  const rounded =
    direction === "down"
      ? Math.floor(raw / quantum) * quantum
      : Math.ceil(raw / quantum) * quantum;
  return Math.max(direction === "down" ? 1 : 5, rounded);
}

function compactMinutes(minutes: number) {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h ${rest}m` : `${hours}h`;
}

/** A practical range rather than a false minute-perfect countdown. */
export function etaWindow(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "ETA updating";
  if (seconds < 45) return "Less than a minute remaining";
  const low = roundedMinutes(seconds * 0.8, "down");
  const high = Math.max(low + 5, roundedMinutes(seconds * 1.35, "up"));
  return `About ${compactMinutes(low)}–${compactMinutes(high)} remaining`;
}

export function technicalDuration(seconds: number | null | undefined) {
  if (seconds == null || !Number.isFinite(seconds)) return "unavailable";
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}
