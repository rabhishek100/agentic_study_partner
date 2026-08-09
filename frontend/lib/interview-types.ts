export type InterviewSourceKind = "book" | "video";
export type InterviewMode = "realistic" | "guided";
export type InterviewFormat = "concept" | "system_design" | "source_led";
export type InterviewFormatChoice = "auto" | InterviewFormat;
export type TargetLevel = "entry" | "mid" | "senior";
export type WorkSampleKind =
  | "none"
  | "architecture_diagram"
  | "equation_derivation"
  | "code"
  | "assumptions";
export type InterviewStatus =
  | "ready"
  | "active"
  | "paused"
  | "completed"
  | "abandoned";

export interface InterviewCitation {
  marker: string;
  node_id: number | null;
  page: number | null;
  evidence_rank: number | null;
  start_ms: number | null;
}

export interface InterviewScores {
  technical_correctness: number;
  depth_completeness: number;
  reasoning_structure: number;
  tradeoff_awareness: number;
  communication_clarity: number;
  independence: number;
}

export interface InterviewClarification {
  candidate_question: string;
  interviewer_response: string;
}

export interface InterviewQuestion {
  topic_key: string;
  topic_label: string;
  kind: "primary" | "follow_up" | "clarifying" | "hint" | "synthesis";
  text: string;
  expected_points: string[];
  suggested_answer: string;
  citation_markers: string[];
  difficulty: TargetLevel;
  interviewer_note: string;
  work_sample: WorkSampleKind;
  work_sample_prompt: string | null;
  clarifications: InterviewClarification[];
}

export interface ScreenObservation {
  summary: string;
  strengths: string[];
  issues: string[];
  follow_up: string | null;
}

export interface AnswerEvaluation {
  classification:
    | "source_aligned"
    | "correct_extension"
    | "partially_correct"
    | "incorrect"
    | "insufficient";
  scores: InterviewScores;
  strengths: string[];
  gaps: string[];
  concise_feedback: string;
  recommended_answer: string;
  citation_markers: string[];
  needs_clarifying_probe: boolean;
  clarifying_probe: string | null;
  needs_external_verification: boolean;
  external_query: string | null;
  extension_summary: string | null;
  topic_complete: boolean;
}

export interface WebSource {
  title: string;
  url: string;
  snippet: string;
  rank: number;
}

export interface InterviewTurn {
  turn_index: number;
  question: InterviewQuestion;
  answer_text: string | null;
  transcript_corrected: boolean;
  evaluation: AnswerEvaluation | null;
  interviewer_reaction: string;
  citations: InterviewCitation[];
  web_sources: WebSource[];
  screen_observation: ScreenObservation | null;
  hints_used: number;
  cost_usd: number;
  created_at: string | null;
  answered_at: string | null;
}

export interface TopicState {
  key: string;
  label: string;
  required: boolean;
  attempts: number;
  hints_used: number;
  completed: boolean;
  best_score: number;
}

export interface InterviewCheckpoint {
  topics: TopicState[];
  active_topic_key: string | null;
  strong_streak: number;
  questions_asked: number;
  screen_observation: ScreenObservation | null;
  closing_reason: string | null;
}

export interface InterviewMetrics {
  questions_answered: number;
  topics_covered: number;
  topics_required: number;
  overall_score: number | null;
  dimension_scores: Record<string, number>;
  classification_counts: Record<string, number>;
  strengths: string[];
  revision_topics: string[];
}

export interface InterviewSession {
  session_id: string;
  source_kind: InterviewSourceKind;
  book_id: number | null;
  node_id: number | null;
  video_id: string | null;
  scope_key: string;
  title: string;
  source_title: string;
  interview_format: InterviewFormat;
  format_source: "detected" | "override";
  feedback_mode: InterviewMode;
  target_level: TargetLevel;
  maximum_duration_minutes: number;
  estimated_min_minutes: number;
  estimated_max_minutes: number;
  status: InterviewStatus;
  elapsed_seconds: number;
  started_at: string | null;
  completed_at: string | null;
  checkpoint: InterviewCheckpoint;
  metrics: InterviewMetrics;
  total_cost_usd: number;
  turns: InterviewTurn[];
  created_at: string | null;
  updated_at: string | null;
}

export interface InterviewPreflight {
  source_kind: InterviewSourceKind;
  scope_key: string;
  title: string;
  source_title: string;
  detected_format: InterviewFormat;
  selected_format: InterviewFormat;
  format_source: "detected" | "override";
  topic_count: number;
  required_topic_count: number;
  estimated_min_minutes: number;
  estimated_max_minutes: number;
  warnings: string[];
}

export interface InterviewReport {
  session: InterviewSession;
  missed_topics: string[];
  evidence_confidence: string;
  suggested_next_steps: string[];
}

export const INTERVIEW_DURATIONS = [15, 30, 45, 60, 90, 120] as const;

export function pendingTurn(session: InterviewSession): InterviewTurn | null {
  return [...session.turns].reverse().find((turn) => !turn.answer_text) ?? null;
}

export function appendTranscriptSegment(draft: string, segment: string): string {
  return [draft.trim(), segment.trim()].filter(Boolean).join(" ");
}

export function spokenInterviewQuestion(question: InterviewQuestion): string {
  return [
    question.text.trim(),
    question.work_sample !== "none" ? question.work_sample_prompt?.trim() : "",
  ]
    .filter(Boolean)
    .join(" ");
}

export function workSampleLabel(kind: WorkSampleKind): string {
  switch (kind) {
    case "architecture_diagram":
      return "Architecture exercise";
    case "equation_derivation":
      return "Equation exercise";
    case "code":
      return "Coding exercise";
    case "assumptions":
      return "Assumptions exercise";
    default:
      return "Screen exercise";
  }
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  if (minutes === 60) return "1 hour";
  if (minutes === 90) return "1½ hours";
  return "2 hours";
}
