/**
 * Mirrors of the FastAPI response contracts.
 *
 * These correspond one-to-one with the Pydantic models in `study/contracts.py`
 * and `api/main.py`. They are hand-maintained: when a contract changes on the
 * server, changing it here is what makes the compiler point at every call site
 * that has to follow.
 */

export type Route =
  | "hierarchy_summary"
  | "hierarchy_list"
  | "retrieval_qa"
  | "prior_answer_transform"
  | "clarify";

export type HistoryDependency = "independent" | "dependent" | "ambiguous";

export type Outcome = "answer" | "clarify" | "abstain" | "error";

export type RetrievalMode = "bm25" | "vector" | "hybrid" | "hybrid_rerank";

export interface ScopeRef {
  kind: "book" | "chapter" | "section";
  book_id: number;
  node_id: number | null;
  display_path: string;
  start_page: number;
  end_page: number;
}

export interface EvidenceRef {
  node_id: number;
  pages: number[];
  path: string;
  /**
   * Populated by the server on every fresh result. Nullable only because a
   * conversation state serialized before these fields existed still loads;
   * see the note on `EvidenceRef` in study/contracts.py.
   */
  book_id: number | null;
  book_title: string | null;
  rank: number | null;
  chunk_id: string | null;
  chunk_index: number | null;
  retrieval_method: string | null;
  score: number | null;
  excerpt: string | null;
}

export interface CitationRef {
  marker: string;
  node_id: number;
  page: number;
  book_id: number | null;
  evidence_rank: number | null;
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  turn_id: string | null;
}

export interface ConversationState {
  conversation_id: string;
  /** Books this conversation may search; empty means the whole library. */
  book_ids: number[];
  messages: ConversationMessage[];
  active_scope: ScopeRef | null;
  pending_clarification: string | null;
  previous_answer: string | null;
  previous_evidence: EvidenceRef[];
  previous_citations: CitationRef[];
  previous_route: Route | null;
}

export interface TurnResult {
  question: string;
  answer: string;
  route: Route;
  history_dependency: HistoryDependency;
  standalone_query: string | null;
  resolved_scope: ScopeRef | null;
  evidence: EvidenceRef[];
  citations: CitationRef[];
  outline_node_ids: number[];
  outcome: Outcome;
  retrieval_mode: string | null;
  warnings: string[];
}

export interface ChatResponse {
  result: TurnResult;
  state: ConversationState;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  book_ids: number[];
  retrieval_mode: RetrievalMode;
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export interface ConversationListResponse {
  conversations: ConversationSummary[];
}

export interface StoredTurn {
  turn_index: number;
  question: string;
  answer: string;
  result: TurnResult;
  created_at: string;
}

export interface ConversationDetail {
  conversation_id: string;
  title: string;
  book_ids: number[];
  retrieval_mode: RetrievalMode;
  created_at: string;
  updated_at: string;
  turns: StoredTurn[];
}

export interface BookSummary {
  book_id: number;
  title: string;
  author: string | null;
  page_count: number | null;
  ready_at: string | null;
  chunk_count: number;
  embedding_count: number;
  retrieval_complete: boolean;
}

export interface BookListResponse {
  books: BookSummary[];
}

/** Ingestion job lifecycle, as reported by `api/ingestions.py`. */
export type JobStatus =
  | "awaiting_upload"
  | "queued"
  | "validating"
  | "parsing"
  | "persisting"
  | "chunking"
  | "embedding"
  | "verifying"
  | "retry_scheduled"
  | "ready"
  | "failed"
  | "cancelled";

export type StageState = "done" | "active" | "pending";

export interface StageView {
  stage: string;
  label: string;
  state: StageState;
  expected_seconds: number;
  elapsed_seconds: number | null;
}

export interface JobTiming {
  percent: number;
  elapsed_seconds: number;
  estimated_total_seconds: number;
  estimated_remaining_seconds: number | null;
  overrunning: boolean;
  stages: StageView[];
}

export interface JobProgress {
  completed: number;
  total: number | null;
  unit: string | null;
  percent: number | null;
}

export interface JobError {
  code: string;
  message: string;
}

export interface IngestionJob {
  job_id: string;
  status: JobStatus;
  stage: string | null;
  original_filename: string;
  progress: JobProgress;
  attempt: number;
  max_attempts: number;
  retryable: boolean;
  page_count: number | null;
  book_id: number | null;
  error: JobError | null;
  cancellation_requested: boolean;
  timing: JobTiming;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
}

export interface IngestionJobList {
  jobs: IngestionJob[];
}

/** The create response is deliberately narrower than a polled job. */
export interface CreateIngestionResponse {
  job_id: string;
  status: JobStatus;
  storage_bucket: string;
  storage_path: string;
  maximum_bytes: number;
  upload_method: "tus";
}

/** A chat turn as the interface holds it, before conversations are persisted. */
export interface ChatTurn {
  id: string;
  question: string;
  /** Text streamed or settled so far. */
  answer: string;
  status: "streaming" | "complete" | "stopped" | "failed";
  /** Present once the turn settles; drives references and the inspector. */
  result: TurnResult | null;
  error: string | null;
}
