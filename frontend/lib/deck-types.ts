/**
 * Mirrors of `decks/contracts.py`. The interface renders decks; it never
 * derives a card, a citation, or a schedule of its own.
 */

export type CardType = "qa" | "concept" | "mcq" | "system_design";
export type Difficulty = "foundational" | "intermediate" | "advanced";
export type SourceKind = "book" | "video";
export type DeckStatus = "generating" | "ready" | "partial" | "failed";
export type ReviewStateName = "new" | "learning" | "review" | "relearning";
export type GenerationMode = "topic_generated" | "book_extracted";
export type AnswerSource = "printed_in_book" | "rag_generated";

/** Anki's four. 1 is a failure; 4 means it was instant. */
export type Rating = 1 | 2 | 3 | 4;

export interface DeckCitation {
  marker: string;
  node_id: number | null;
  page: number | null;
  evidence_rank: number | null;
  start_ms: number | null;
  frame_id: number | null;
}

export interface DeckFigure {
  kind: "book_image" | "lecture_frame";
  caption: string | null;
  book_id: number | null;
  node_id: number | null;
  block_id: number | null;
  page: number | null;
  mime_type: string | null;
  frame_id: number | null;
  start_ms: number | null;
}

export interface McqOption {
  label: "A" | "B" | "C" | "D";
  text: string;
  correct: boolean;
  rationale: string;
}

export interface CardBack {
  answer: string;
  key_points: string[];
  say_it_aloud: string;
  why_it_matters: string;
  options: McqOption[];
  components: string[];
  data_flow: string[];
  trade_offs: string[];
  failure_modes: string[];
}

export interface DeckCard {
  card_id: string | null;
  topic_key: string;
  card_index: number;
  card_type: CardType;
  front: string;
  back: CardBack;
  citations: DeckCitation[];
  figures: DeckFigure[];
  interview_priority: number;
  priority_reason: string;
  difficulty: Difficulty;
  /** Model knowledge, labelled as such. Never counted as grounded. */
  interview_angle: string | null;
  answer_source?: AnswerSource | null;
}

export interface DeckMetrics {
  topics_total: number;
  topics_required: number;
  topics_covered: number;
  uncovered_topic_labels: string[];
  source_questions_total: number;
  source_questions_covered: number;
  uncovered_question_labels: string[];
  cards_generated: number;
  cards_kept: number;
  cards_dropped_uncited: number;
  cards_dropped_out_of_scope: number;
  cards_dropped_duplicate: number;
  cards_dropped_malformed: number;
  cards_with_interview_angle: number;
  card_type_counts: Record<string, number>;
  priority_counts: Record<string, number>;
  repair_attempted: boolean;
  notice?: string | null;
}

export interface DeckSummary {
  deck_id: string;
  source_kind: SourceKind;
  generation_mode?: GenerationMode;
  scope_key: string;
  version: number;
  title: string;
  source_title: string;
  status: DeckStatus;
  card_count: number;
  topic_count: number;
  book_id: number | null;
  node_id: number | null;
  video_id: string | null;
  metrics: DeckMetrics;
  due_count: number;
  new_count: number;
  updated_at: string | null;
}

export interface ReviewState {
  state: ReviewStateName;
  due_at: string | null;
  interval_days: number;
  ease: number;
  reps: number;
  lapses: number;
  last_reviewed_at: string | null;
  last_rating: number | null;
}

export interface QueueCard {
  card: DeckCard;
  deck_id: string;
  deck_title: string;
  source_kind: SourceKind;
  source_title: string;
  book_id: number | null;
  video_id: string | null;
  review: ReviewState;
}

export interface ReviewQueue {
  cards: QueueCard[];
  due_total: number;
  new_total: number;
  reviewed_today: number;
  new_cards_per_day: number;
  max_reviews_per_day: number;
}

export interface DeckPreferences {
  new_cards_per_day: number;
  max_reviews_per_day: number;
}

export interface DeckJob {
  job_id: string;
  source_kind: SourceKind;
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  scope_key: string;
  generation_mode?: GenerationMode;
  book_id: number | null;
  node_id: number | null;
  video_id: string | null;
  deck_id: string | null;
  topics_total: number;
  topics_done: number;
  progress: number;
  attempt_count: number;
  error_code: string | null;
  error_detail: string | null;
  title: string;
  source_title: string;
  created_at: string | null;
  updated_at: string | null;
  timing: DeckJobTiming;
}

export interface DeckJobStage {
  stage: string;
  label: string;
  state: "done" | "active" | "pending";
  expected_seconds: number;
  elapsed_seconds: number | null;
}

export interface DeckJobTiming {
  percent: number;
  elapsed_seconds: number;
  estimated_total_seconds: number;
  estimated_remaining_seconds: number | null;
  overrunning: boolean;
  stages: DeckJobStage[];
}

export interface DeckListResponse {
  decks: DeckSummary[];
  jobs: DeckJob[];
}

export interface DeckDetailResponse {
  deck: DeckSummary;
  cards: QueueCard[];
}

/** What the card side-chat endpoint returns: an ordinary side-chat thread. */
export interface DeckConversationResponse {
  conversation_id: string;
}

export interface ChapterSummary {
  node_id: number;
  title: string;
  path_text: string;
  start_page: number;
  end_page: number;
}

export interface ChapterListResponse {
  book_id: number;
  chapters: ChapterSummary[];
}

export const CARD_TYPE_LABELS: Record<CardType, string> = {
  qa: "Interview question",
  concept: "Concept",
  mcq: "Multiple choice",
  system_design: "System design",
};

export const RATING_LABELS: Record<Rating, string> = {
  1: "Again",
  2: "Hard",
  3: "Good",
  4: "Easy",
};

/** A deck is live work when its job has not settled. */
export function jobIsLive(job: DeckJob): boolean {
  return job.status === "queued" || job.status === "running";
}

export function formatDeckDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  const remainder = whole % 60;
  if (minutes < 60) return remainder ? `${minutes}m ${remainder}s` : `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const minuteRemainder = minutes % 60;
  return minuteRemainder ? `${hours}h ${minuteRemainder}m` : `${hours}h`;
}

export function deckJobError(job: DeckJob): {
  title: string;
  message: string;
  reference: string;
} {
  const extracted = job.generation_mode === "book_extracted";
  const fallbackTitle = extracted
    ? "We couldn’t extract these questions"
    : "We couldn’t finish this deck";
  const copy: Record<string, { title: string; message: string }> = {
    source_unavailable: {
      title: "The source is not ready",
      message: extracted
        ? "We couldn’t find enough readable chapter content to extract its questions. Check the source and try again."
        : "We couldn’t find enough readable source material for this deck. Check the source and try again.",
    },
    generation_failed: {
      title: fallbackTitle,
      message:
        "The AI service couldn’t complete the run. Your existing cards were not changed.",
    },
    invalid_scope: {
      title: "The selected source is no longer available",
      message:
        "Choose the chapter or lecture again, then start a new generation.",
    },
    unexpected_error: {
      title: fallbackTitle,
      message:
        "Something interrupted generation. Your existing cards were not changed.",
    },
  };
  const safe = copy[job.error_code ?? ""] ?? {
    title: fallbackTitle,
    message: "Generation stopped before the new deck was saved. Please try again.",
  };
  const compactId = job.job_id.replace(/[^a-z0-9]/gi, "").slice(0, 6).toUpperCase();
  return { ...safe, reference: `DECK-${compactId || "UNKNOWN"}` };
}

/**
 * How long until this card comes back, in the words a person would use.
 *
 * Short day-counts keep a decimal. The four grading buttons sit side by side
 * and are chosen by comparing them, so rounding 2.5 and 3.25 both to "3 d"
 * hides the difference the reader is actually deciding between.
 */
export function describeInterval(days: number): string {
  if (days <= 0) return "now";
  const minutes = days * 1440;
  if (minutes < 60) return `${Math.round(minutes)} min`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h`;
  if (days < 10) {
    const rounded = Math.round(days * 10) / 10;
    return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} d`;
  }
  if (days < 30) return `${Math.round(days)} d`;
  if (days < 365) return `${Math.round(days / 30)} mo`;
  return `${(days / 365).toFixed(1)} y`;
}

export function coveragePercent(metrics: DeckMetrics): number {
  if (metrics.source_questions_total) {
    return Math.round(
      (metrics.source_questions_covered / metrics.source_questions_total) * 100,
    );
  }
  if (!metrics.topics_required) return 100;
  return Math.round((metrics.topics_covered / metrics.topics_required) * 100);
}
