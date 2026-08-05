import type { VideoSummary } from "./video-types";

/**
 * What a lecture is, from the point of view of someone deciding what to do
 * with it.
 *
 * The server tracks readiness and a job status separately, and the list showed
 * both raw: a badge reading "Ready (partial)" beside four reading "Failed",
 * with the one usable lecture buried among them. The reader's question is
 * simpler than either field — can I ask this lecture something, is it still
 * working, or does it need me — so that is what the interface sorts by.
 */
export type VideoState =
  | "ready"
  | "partial"
  | "processing"
  | "awaiting_upload"
  | "failed"
  | "cancelled";

export function videoState(video: VideoSummary): VideoState {
  const job = video.latest_ingestion;
  if (job?.status === "awaiting_upload") return "awaiting_upload";
  if (video.readiness_status === "failed") return "failed";
  if (job?.status === "cancelled" && !video.ready_for_qa) return "cancelled";
  if (video.readiness_status === "processing") return "processing";
  // "Degraded" is the server's word for a published version that missed a
  // quality gate. Without a note saying which, it is a worry with no content,
  // so a version that cannot explain itself is simply ready.
  if (video.readiness_status === "degraded" && video.readiness_notes.length > 0) {
    return "partial";
  }
  return "ready";
}

/** Whether opening the workspace would give the reader anything to do. */
export function isUsable(state: VideoState): boolean {
  return state === "ready" || state === "partial";
}

export type VideoGroupKey = "library" | "processing" | "attention";

export interface VideoGroup {
  key: VideoGroupKey;
  title: string;
  /** Said once per group rather than repeated on every card inside it. */
  description: string;
  videos: VideoSummary[];
}

const GROUP_OF: Record<VideoState, VideoGroupKey> = {
  ready: "library",
  partial: "library",
  processing: "processing",
  awaiting_upload: "attention",
  failed: "attention",
  cancelled: "attention",
};

const GROUPS: { key: VideoGroupKey; title: string; description: string }[] = [
  {
    key: "library",
    title: "Ready to ask",
    description: "Open a lecture to ask about anything in it.",
  },
  {
    key: "processing",
    title: "Processing",
    description:
      "Still being read. This continues if you leave the page, and questions unlock when it finishes.",
  },
  {
    key: "attention",
    title: "Needs attention",
    description: "These are not ready and will not become ready on their own.",
  },
];

/**
 * Group videos for display, keeping the library first.
 *
 * Empty groups are dropped rather than shown empty: a "Needs attention"
 * heading over nothing is a small false alarm every time the page loads.
 */
export function groupVideos(videos: VideoSummary[]): VideoGroup[] {
  const byGroup = new Map<VideoGroupKey, VideoSummary[]>();
  for (const video of videos) {
    const key = GROUP_OF[videoState(video)];
    const existing = byGroup.get(key);
    if (existing) existing.push(video);
    else byGroup.set(key, [video]);
  }
  return GROUPS.filter((group) => byGroup.get(group.key)?.length).map(
    (group) => ({ ...group, videos: byGroup.get(group.key)! }),
  );
}

/**
 * When a lecture was added, precise enough to tell copies apart.
 *
 * Three uploads of one filename is what a failed re-upload leaves behind, and
 * they are usually minutes apart — so a date alone, the obvious choice, is the
 * one format that cannot separate them. Today's carry a time; older ones do
 * not, because by then the date is the thing that distinguishes them.
 */
export function formatAdded(iso: string, now: Date = new Date()): string {
  const added = new Date(iso);
  if (Number.isNaN(added.getTime())) return "";
  const sameDay = added.toDateString() === now.toDateString();
  if (sameDay) {
    return `today, ${added.toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    })}`;
  }
  const sameYear = added.getFullYear() === now.getFullYear();
  return added.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}
