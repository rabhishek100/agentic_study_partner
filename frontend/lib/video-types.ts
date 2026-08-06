/** Contracts served by the video API, mirrored for the Videos section. */

export type VideoReadiness = "processing" | "ready" | "degraded" | "failed";
export type VideoModality =
  | "transcript"
  | "visual_frame"
  | "visual_event"
  | "resource_page";
export type VideoRetrievalMethod =
  | "fts"
  | "text_vector"
  | "image_vector"
  | "hybrid"
  | "timeline_expansion"
  // Not a search: the complete published transcript, in order.
  | "complete_transcript";

export interface VideoPlayback {
  kind: "youtube" | "local";
  youtube_video_id: string | null;
  media_url: string | null;
}

export interface VideoIngestion {
  job_id: string;
  video_id: string;
  status:
    | "awaiting_upload"
    | "queued"
    | "running"
    | "retry_scheduled"
    | "ready"
    | "failed"
    | "cancelled";
  stage: string | null;
  progress: {
    completed: number;
    total: number | null;
    unit: string | null;
    percent: number | null;
  };
  attempt: number;
  max_attempts: number;
  retryable: boolean;
  cancellation_requested: boolean;
  actual_cost_usd: string;
  cost_cap_usd: string;
  error: { code: string; message: string } | null;
}

export interface VideoSummary {
  video_id: string;
  title: string;
  description: string | null;
  source_kind: "youtube" | "upload";
  duration_ms: number | null;
  readiness_status: VideoReadiness;
  ready_for_qa: boolean;
  playback: VideoPlayback;
  latest_ingestion: VideoIngestion | null;
  /** Measured sentences for the quality gates a published version missed. */
  readiness_notes: string[];
  /** Whether removal would succeed; the server owns the constraint. */
  deletable: boolean;
  created_at: string;
  updated_at: string;
  ready_at: string | null;
}

export interface VideoResource {
  resource_id: string;
  resource_kind: "pdf" | "external_link";
  origin: "upload" | "url" | "discovered";
  status: "pending" | "processing" | "ready" | "failed";
  title: string;
  source_url: string | null;
  page_count: number | null;
  role: "slides" | "notes" | "reference";
  required: boolean;
}

export interface VideoChapter {
  chapter_index: number;
  chapter_kind: "youtube" | "manual" | "derived";
  title: string;
  start_ms: number;
  end_ms: number;
}

export interface VideoDetail extends VideoSummary {
  source: {
    source_kind: "youtube" | "upload";
    status: "pending" | "acquiring" | "ready" | "failed";
    source_url: string | null;
    youtube_video_id: string | null;
    original_filename: string | null;
  };
  chapters: VideoChapter[];
  resources: VideoResource[];
  quality_gates: Record<string, unknown>;
}

export interface VideoListResponse {
  videos: VideoSummary[];
}

export interface VideoEvidenceRef {
  rank: number;
  evidence_id: string;
  modality: VideoModality;
  excerpt: string;
  retrieval_method: VideoRetrievalMethod;
  score: number;
  start_ms: number | null;
  end_ms: number | null;
  page_number: number | null;
  frame_id: number | null;
  visual_event_id: number | null;
  transcript_segment_id: number | null;
  resource_page_id: number | null;
  resource_id: string | null;
  resource_title: string | null;
}

export interface VideoCitationRef {
  marker: string;
  evidence_rank: number;
  modality: VideoModality;
  start_ms: number | null;
  page_number: number | null;
  frame_id: number | null;
  resource_id: string | null;
}

/**
 * A request to open a linked document at a particular page.
 *
 * Carries the excerpt as well as the page because the viewer highlights the
 * cited passage, and a slide page can hold several claims — landing on the
 * right page still leaves the reader hunting for the sentence.
 */
export interface VideoDocumentTarget {
  resourceId: string;
  page: number;
  excerpt?: string | null;
}

export interface VideoVisualCard {
  evidence_rank: number;
  frame_id: number | null;
  visual_event_id: number | null;
  start_ms: number;
  end_ms: number | null;
  summary: string;
  kind: "frame" | "transition";
}

export interface VideoTurnResult {
  question: string;
  answer: string;
  route:
    | "evidence_qa"
    | "lecture_summary"
    | "topic_inventory"
    | "prior_answer_transform"
    | "clarify";
  history_dependency: "independent" | "dependent" | "ambiguous";
  standalone_query: string | null;
  evidence: VideoEvidenceRef[];
  citations: VideoCitationRef[];
  visual_cards: VideoVisualCard[];
  outcome: "answer" | "clarify" | "abstain" | "error";
  ingestion_version_id: string | null;
  retrieval_attempts: number;
  sufficiency_reason: string | null;
  routing_reason: string | null;
  cost_usd: number;
  trace_id: string | null;
  warnings: string[];
}

export interface VideoAskResponse {
  conversation_id: string;
  result: VideoTurnResult;
}

export interface VideoConversationSummary {
  conversation_id: string;
  video_id: string;
  video_title: string;
  title: string;
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export interface VideoConversationDetail {
  conversation_id: string;
  video_id: string;
  title: string;
  turns: {
    turn_index: number;
    question: string;
    answer: string | null;
    result: VideoTurnResult | null;
    cost_usd: number;
    trace_id: string | null;
    created_at: string;
  }[];
}

export interface VideoTimelineEntry {
  frame_id: number;
  timestamp_ms: number;
  summary: string | null;
  visual_types: string[];
  ocr_text: string | null;
  image_url: string;
}

export interface VideoTurn {
  id: string;
  question: string;
  answer: string;
  status: "streaming" | "complete" | "failed" | "stopped";
  result: VideoTurnResult | null;
  error: string | null;
}

/** "12:04" or "1:02:33" from milliseconds. */
export function formatTimestamp(milliseconds: number | null): string {
  if (milliseconds === null || Number.isNaN(milliseconds)) return "--:--";
  const total = Math.max(0, Math.floor(milliseconds / 1000));
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  const padded = `${minutes.toString().padStart(hours ? 2 : 1, "0")}:${seconds
    .toString()
    .padStart(2, "0")}`;
  return hours ? `${hours}:${padded}` : padded;
}
