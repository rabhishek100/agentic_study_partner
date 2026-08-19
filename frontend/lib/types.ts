/**
 * Mirrors of the FastAPI response contracts.
 *
 * These correspond one-to-one with the Pydantic models in `study/contracts.py`
 * and `api/main.py`. They are hand-maintained: when a contract changes on the
 * server, changing it here is what makes the compiler point at every call site
 * that has to follow.
 */

export type Route =
  | "library_list"
  | "hierarchy_summary"
  | "hierarchy_list"
  | "retrieval_qa"
  | "prior_answer_transform"
  | "clarify"
  | "external_qa";

export interface WebSourceRef {
  url: string;
  title: string;
  snippet: string;
  domain?: string | null;
  rank?: number | null;
}

export type HistoryDependency = "independent" | "dependent" | "ambiguous";

export type Outcome = "answer" | "clarify" | "abstain" | "error";

export type RetrievalMode = "bm25" | "vector" | "hybrid" | "hybrid_rerank";
export type ResponseDepth = "quick" | "interview" | "deep";
export type AnswerArchetype =
  | "concept_explanation"
  | "system_design"
  | "chapter_review"
  | "answer_transform";

export interface PromptProfile {
  interview_instructions: string;
  concept_template: string;
  system_design_template: string;
  chapter_review_template: string;
  user_prompt_template: string;
}

export interface PromptSettingsResponse {
  profile: PromptProfile;
  defaults: PromptProfile;
  locked_system_prompt: string;
  preview_system_prompt: string;
  preview_user_prompt: string;
  profile_version: string;
}

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

export interface FigureRef {
  book_id: number;
  node_id: number;
  block_id: number;
  page: number;
  mime_type: string;
  path: string;
  caption: string | null;
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

/** One passage a reader carried from a conversation into a side chat. */
export interface QuoteAnchor {
  /** Absent on anchors stored before source anchors existed. */
  kind?: "answer_quote";
  anchor_id: string;
  parent_turn_index: number;
  quoted_text: string;
}

/**
 * An anchor pointing at the source rather than at an answer.
 *
 * The distinction is not cosmetic: a quote is generated text and can never be
 * cited, while a page of the reader's own book is matched back to canonical
 * content and cited as normal. The server does that matching; the client only
 * ever says where the reader was.
 */
export interface DocumentPageAnchor {
  kind: "document_page";
  anchor_id: string;
  book_id: number;
  page: number;
}

export interface DocumentPassageAnchor {
  kind: "document_passage";
  anchor_id: string;
  book_id: number;
  page: number;
  selected_text: string;
}

/**
 * Where the viewer is in a lecture, as an instant.
 *
 * The server widens it into a window when it resolves — biased backwards,
 * because a question asked at 12:04 is nearly always about what was just
 * said. The client sends the instant, not the window: how far "here" reaches
 * is a property of the material rather than of the click.
 */
export interface LectureMomentAnchor {
  kind: "lecture_moment";
  anchor_id: string;
  video_id: string;
  timestamp_ms: number;
}

/** A span of lecture the viewer marked deliberately. */
export interface LectureStretchAnchor {
  kind: "lecture_stretch";
  anchor_id: string;
  video_id: string;
  start_ms: number;
  end_ms: number;
}

export type DocumentAnchor = DocumentPageAnchor | DocumentPassageAnchor;
export type LectureAnchor = LectureMomentAnchor | LectureStretchAnchor;
export type SourceAnchor = DocumentAnchor | LectureAnchor;
export type Anchor = QuoteAnchor | SourceAnchor;

export function isQuoteAnchor(anchor: Anchor): anchor is QuoteAnchor {
  return anchor.kind === undefined || anchor.kind === "answer_quote";
}

/** Where the reader last was in a source. */
export interface SourcePosition {
  page: number;
}

export interface ReadingSession {
  conversation_id: string;
  book_id: number;
  title: string;
  document_type: string;
  position: SourcePosition | null;
  question_count: number;
  updated_at: string;
}

/** What a side turn was given, and what its token budget excluded. */
export interface SideContextReport {
  anchor_ids: string[];
  pinned_chunk_ids: string[];
  token_count: number;
  token_budget: number;
  dropped: string[];
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
  figures: FigureRef[];
  outline_node_ids: number[];
  outcome: Outcome;
  retrieval_mode: string | null;
  warnings: string[];
  answer_archetype: AnswerArchetype | null;
  response_depth: ResponseDepth | null;
  routing_reason: string | null;
  prompt_profile_version: string | null;
  /** Present only on a side-chat turn. */
  side_context: SideContextReport | null;
  web_sources?: WebSourceRef[];
  source_type?: "book_library" | "model_knowledge" | "web_search";
}

export interface ChatResponse {
  result: TurnResult;
  state: ConversationState;
  /**
   * Which recorded turn this is. Not derivable from the client's own list: a
   * stopped turn is never recorded and a regenerated one is recorded twice, so
   * list position and turn index diverge. A side chat anchors to this index.
   */
  turn_index: number;
}

export interface BookSourceResponse {
  book_id: number;
  url: string;
  expires_at: string;
  page_count: number | null;
}

export interface ConversationSummary {
  conversation_id: string;
  title: string;
  book_ids: number[];
  retrieval_mode: RetrievalMode;
  turn_count: number;
  created_at: string;
  updated_at: string;
  /** Side chats opened over this conversation, counted rather than listed. */
  side_thread_count: number;
}

export interface SideChatSummary {
  conversation_id: string;
  parent_conversation_id: string;
  title: string;
  book_ids: number[];
  retrieval_mode: RetrievalMode;
  anchors: Anchor[];
  turn_count: number;
  created_at: string;
  updated_at: string;
}

export interface SideChatListResponse {
  side_chats: SideChatSummary[];
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
  prompt_profile: PromptProfile;
  created_at: string;
  updated_at: string;
  turns: StoredTurn[];
  /** Set when this conversation is a side chat. */
  parent_conversation_id: string | null;
  anchors: Anchor[];
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
  document_type?: "book" | "paper";
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
  | "captioning"
  | "chunking"
  | "embedding"
  | "verifying"
  | "classifying"
  | "ocr"
  | "needs_toc_review"
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
  /** Time the pipeline worked. Excludes any wait for a reviewer. */
  elapsed_seconds: number;
  estimated_total_seconds: number;
  /** Null while overrunning, and while waiting on a person. */
  estimated_remaining_seconds: number | null;
  overrunning: boolean;
  stages: StageView[];
  awaiting_input: boolean;
  awaiting_input_seconds: number;
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
  document_type?: "book" | "paper";
  error: JobError | null;
  cancellation_requested: boolean;
  /**
   * Whether this tab can act on the job. False for a job whose source was
   * imported from an operator's filesystem: no browser can advance it, so the
   * upload panel shows it without adopting it.
   */
  driveable: boolean;
  timing: JobTiming;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
}

export interface IngestionJobList {
  jobs: IngestionJob[];
}

export interface CreateIngestionRequest {
  original_filename: string;
  content_type?: string | null;
  content_length?: number | null;
  document_type?: "book" | "paper";
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

export interface IngestionLimitsResponse {
  maximum_bytes: number;
  maximum_pages: number;
  allowed_content_types: string[];
}

export interface OutlineEntry {
  level: number;
  title: string;
  page: number;
}

export interface OutlineReview {
  job_id: string;
  status: JobStatus;
  page_count: number;
  outline_source: string;
  proposer_version: string;
  reasons: string[];
  warnings: string[];
  entries: OutlineEntry[];
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
  /** Per-turn @book narrowing, retained so retry repeats the same scope. */
  mentionedBookIds?: number[];
  /**
   * The index this turn was recorded under, once it has been. Absent for a
   * turn that never reached the server, which is exactly the turn a side chat
   * must refuse to anchor to.
   */
  turnIndex?: number;
}

export interface SuggestedQuestionsResponse {
  questions: string[];
  scope_type: string;
  scope_key: string;
}
