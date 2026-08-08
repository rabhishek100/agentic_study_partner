/**
 * Mirrors of `decks/contracts.py`. The interface renders decks; it never
 * derives a card, a citation, or a schedule of its own.
 */

export type CardType = "qa" | "concept" | "mcq" | "system_design";
export type Difficulty = "foundational" | "intermediate" | "advanced";
export type SourceKind = "book" | "video";
export type DeckStatus = "generating" | "ready" | "partial" | "failed";
export type ReviewStateName = "new" | "learning" | "review" | "relearning";

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
}

export interface DeckMetrics {
  topics_total: number;
  topics_required: number;
  topics_covered: number;
  uncovered_topic_labels: string[];
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
}

export interface DeckSummary {
  deck_id: string;
  source_kind: SourceKind;
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
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  scope_key: string;
  deck_id: string | null;
  topics_total: number;
  topics_done: number;
  progress: number;
  attempt_count: number;
  error_code: string | null;
  error_detail: string | null;
}

export interface DeckListResponse {
  decks: DeckSummary[];
  jobs: DeckJob[];
}

export interface DeckDetailResponse {
  deck: DeckSummary;
  cards: QueueCard[];
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
  if (!metrics.topics_required) return 100;
  return Math.round((metrics.topics_covered / metrics.topics_required) * 100);
}
