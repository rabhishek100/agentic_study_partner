import type {
  VideoCitationRef,
  VideoEvidenceRef,
  VideoSummary,
} from "@/lib/video-types";

export interface CourseSummary {
  course_id: string;
  title: string;
  description: string | null;
  preview_youtube_video_id: string | null;
  lecture_count: number;
  ready_count: number;
  degraded_count: number;
  processing_count: number;
  failed_count: number;
  ingestion_cost_cap_usd: number;
  actual_ingestion_cost_usd: number;
  created_at: string;
  updated_at: string;
}

export interface CourseLecture extends VideoSummary {
  lecture_index: number;
  title_override: string | null;
  display_title: string;
}

export interface CourseDetail extends CourseSummary {
  lectures: CourseLecture[];
}

export interface CourseListResponse {
  courses: CourseSummary[];
}

export interface CourseEvidenceRef extends VideoEvidenceRef {
  video_id: string;
  video_title: string;
  lecture_index: number;
  ingestion_version_id: string;
}

export interface CourseCitationRef extends VideoCitationRef {
  video_id: string;
  video_title: string;
  lecture_index: number;
}

export interface CourseTurnResult {
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
  evidence: CourseEvidenceRef[];
  citations: CourseCitationRef[];
  outcome: "answer" | "clarify" | "abstain" | "error";
  retrieval_attempts: number;
  sufficiency_reason: string | null;
  routing_reason: string | null;
  cost_usd: number;
  trace_id: string | null;
  excluded_video_ids: string[];
  warnings: string[];
}

export interface CourseConversationSummary {
  conversation_id: string;
  course_id: string;
  title: string;
  selected_video_ids: string[];
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export interface CourseConversationListResponse {
  conversations: CourseConversationSummary[];
}

export interface CourseTurnView {
  turn_index: number;
  question: string;
  answer: string | null;
  result: CourseTurnResult | null;
  cost_usd: number;
  trace_id: string | null;
  created_at: string;
}

export interface CourseConversationDetail {
  conversation_id: string;
  course_id: string;
  title: string;
  selected_video_ids: string[];
  turns: CourseTurnView[];
}

export interface CourseAskResponse {
  conversation_id: string;
  result: CourseTurnResult;
  turn_index: number;
}

export interface CourseUiTurn {
  id: string;
  question: string;
  answer: string;
  status: "streaming" | "complete" | "failed";
  result: CourseTurnResult | null;
  error?: string;
}
