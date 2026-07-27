import type { CitationRef, EvidenceRef } from "./types";

/**
 * Citation markers, and how they map onto the reference list.
 *
 * The API emits two marker formats, because the two routes ground differently:
 *
 *   [S1]        retrieval QA — an ordinal into the retrieved evidence
 *   [N123:P45]  hierarchy summaries — an exact canonical node and page
 *
 * Both are the grounding contract and stay in the answer text. This module
 * turns them into positions in the turn's reference list so the interface can
 * render them as numbered chips instead of leaving raw brackets in prose.
 */

export const CITATION_PATTERN = /\[S(\d+)]|\[N(\d+):P(\d+)]/g;

export interface CitationMarker {
  /** The literal text matched, e.g. "[S1]" or "[N123:P45]". */
  marker: string;
  /** 1-based position in the reference list, or null when unresolvable. */
  index: number | null;
  evidence: EvidenceRef | null;
  citation: CitationRef | null;
}

export type AnswerSegment =
  | { type: "text"; value: string }
  | ({ type: "citation" } & CitationMarker);

/**
 * Resolve one marker against a turn's evidence and citations.
 *
 * A marker the server did not record — a model that invented `[S9]` when five
 * documents were retrieved, say — resolves to a null index. The renderer keeps
 * it as literal text rather than inventing a chip that points nowhere.
 */
export function resolveMarker(
  marker: string,
  evidence: EvidenceRef[],
  citations: CitationRef[],
): CitationMarker {
  const citation = citations.find((entry) => entry.marker === marker) ?? null;

  const sourceMatch = /^\[S(\d+)]$/.exec(marker);
  if (sourceMatch) {
    const rank = Number(sourceMatch[1]);
    const position = evidence.findIndex((entry) => entry.rank === rank);
    return position === -1
      ? { marker, index: null, evidence: null, citation }
      : {
          marker,
          index: position + 1,
          evidence: evidence[position] ?? null,
          citation,
        };
  }

  const nodeMatch = /^\[N(\d+):P(\d+)]$/.exec(marker);
  if (nodeMatch) {
    const nodeId = Number(nodeMatch[1]);
    const page = Number(nodeMatch[2]);
    // Prefer the reference that actually covers the cited page; a node can
    // appear once in the list while spanning several pages.
    const exact = evidence.findIndex(
      (entry) => entry.node_id === nodeId && entry.pages.includes(page),
    );
    const position =
      exact === -1
        ? evidence.findIndex((entry) => entry.node_id === nodeId)
        : exact;
    return position === -1
      ? { marker, index: null, evidence: null, citation }
      : {
          marker,
          index: position + 1,
          evidence: evidence[position] ?? null,
          citation,
        };
  }

  return { marker, index: null, evidence: null, citation };
}

/**
 * Split answer text into plain runs and resolved citation markers.
 *
 * Markers that resolve to nothing are returned as text, so no information is
 * lost and nothing renders as a dead link.
 */
export function splitOnCitations(
  text: string,
  evidence: EvidenceRef[],
  citations: CitationRef[],
): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let lastIndex = 0;

  // A fresh regex per call: the shared literal carries `g` and therefore
  // mutable `lastIndex` state.
  const pattern = new RegExp(CITATION_PATTERN.source, "g");
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    const resolved = resolveMarker(match[0], evidence, citations);
    if (resolved.index === null) continue;

    if (match.index > lastIndex) {
      segments.push({ type: "text", value: text.slice(lastIndex, match.index) });
    }
    segments.push({ type: "citation", ...resolved });
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    segments.push({ type: "text", value: text.slice(lastIndex) });
  }
  return segments;
}

/** Human-readable hierarchy: "Chapter 1 :: Core idea" reads better as a path. */
export function formatPath(path: string): string[] {
  return path.split(" :: ").filter(Boolean);
}

/** "p. 12" or "pp. 12–15" from a reference's page list. */
export function formatPages(pages: number[]): string {
  if (pages.length === 0) return "";
  const first = pages[0]!;
  const last = pages[pages.length - 1]!;
  return first === last ? `p. ${first}` : `pp. ${first}–${last}`;
}
