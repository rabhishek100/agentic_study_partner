/** Server-authored revision content; the browser only lays it out. */
export interface RevisionItem { id: string; heading: string; text: string; citations: string[] }
export interface RevisionNode { id: string; label: string; citations: string[] }
export interface RevisionEdge extends RevisionNode { source: string; target: string }
export interface RevisionContent {
  template_kind: "chapter" | "paper";
  title: string;
  central_idea: RevisionItem;
  diagram: { description: string; description_citations: string[]; nodes: RevisionNode[]; edges: RevisionEdge[]; source_figure_ids: number[] };
  essential_notes: RevisionItem[];
  comparison_rows: RevisionItem[];
  equation: RevisionItem | null;
  recall_cues: RevisionItem[];
  compression_notes: string[];
  essential_concepts: { id: string; label: string; citations: string[]; item_ids: string[] }[];
  source_dispositions: { source_unit: string; item_ids: string[]; reason: string }[];
}
export interface RevisionReference { book_id: number; node_id: number; page: number; path: string; section_number: string; section_title: string }
export interface RevisionSummary {
  id: string; book_id: number; chapter_node_id: number | null; scope_kind: "chapter" | "paper";
  scope_key: string; version: number; source_title: string; scope_title: string; created_at: string;
}
export interface RevisionSheet extends RevisionSummary {
  content: RevisionContent;
  source_references: Record<string, RevisionReference>;
  source_changed: boolean;
  settings_changed: boolean;
  provenance: {
    html?: string;
    page_count?: number;
    quality_repairs?: number;
    /**
     * What the independent reviewer still wanted after its revisions were
     * spent. Empty or absent means the sheet passed. Non-empty means it was
     * published with known gaps — shown on the sheet, never left implicit,
     * because an imperfect sheet must not be mistaken for a complete one.
     */
    outstanding_findings?: string[];
    review?: { beauty: {score: number; rationale: string}; presentation: {score: number; rationale: string}; concept_coverage: {score: number; rationale: string}; conciseness: {score: number; rationale: string}; coverage: {concept_id: string; status: string; reason: string}[] };
    model: string; prompt_version: string; content_repairs: number; fit_repairs: number; inspected_figures: number[]; uninspected_figures: number[] };
  diagram_layout: {
    width: number; height: number;
    nodes: { id: string; x: number; y: number; width: number; height: number }[];
    edges: { id: string; number: number; points: [number, number][] }[];
  };
}
export interface RevisionJob {
  id: string; scope_key: string; book_id: number; chapter_node_id: number | null;
  status: "queued" | "running" | "ready" | "failed" | "cancelled";
  stage: string; source_title: string; scope_title: string; sheet_id: string | null;
  error_code: string | null; error_detail: string | null; cancellation_requested: boolean;
}
export interface RevisionList { sheets: RevisionSummary[]; jobs: RevisionJob[] }
export interface RevisionCreated { sheet?: RevisionSummary; job?: RevisionJob }
export interface RevisionAnswer { items: RevisionItem[]; insufficient_evidence: string; source_references: Record<string, RevisionReference> }
