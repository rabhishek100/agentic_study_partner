import type { CitationRef, EvidenceRef, FigureRef } from "./types";

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

/**
 * The figures belonging to one resolved citation marker.
 *
 * The two answer routes ground differently, so a figure attaches by whichever
 * evidence the marker actually carries:
 *
 *   [S1]        retrieval QA — the figure shares the marker's evidence rank
 *   [N123:P84]  a summary — the figure is that node's, on that page
 *
 * Keying only on rank silently excluded every summary figure, because a
 * summary's evidence has no rank at all: they were all demoted to the
 * trailing gallery no matter how squarely the answer cited them.
 */
export function figuresForMarker(
  figures: FigureRef[],
  marker: CitationMarker,
): FigureRef[] {
  const { evidence, citation } = marker;

  return figures.filter((figure) => {
    if (
      figure.evidence_rank !== null &&
      evidence?.rank != null &&
      figure.evidence_rank === evidence.rank
    ) {
      return true;
    }
    if (citation && figure.node_id === citation.node_id) {
      return figure.page === citation.page;
    }
    // A summary marker the server did not record still names its node, and
    // the evidence lists the pages that node contributed.
    return (
      evidence != null &&
      figure.node_id === evidence.node_id &&
      evidence.pages.includes(figure.page)
    );
  });
}

/** The page a marker points at, preferring what the citation itself says. */
export function markerPage(marker: CitationMarker): number | null {
  if (marker.citation) return marker.citation.page;
  return marker.evidence?.pages[0] ?? null;
}

/**
 * The markers whose page must be shown, because the chip number alone cannot
 * tell them apart.
 *
 * A chip is numbered by its source's position in the reference list, which
 * says everything worth saying when an answer cites several sources. It says
 * nothing at all when one source is cited at twenty-six different pages — and
 * that is exactly what a chapter summary produces for a book whose table of
 * contents stops at the chapter, because the whole chapter is a single node.
 * Every chip then reads "1", consecutive ones read "1 1 1", and three
 * different pages are indistinguishable from a rendering fault.
 *
 * So the page is added only where the number has stopped discriminating:
 * where one source is cited at more than one page in this answer. A source
 * cited once keeps its bare number, which is the ordinary case and the one
 * the numbering was designed for.
 */
export function ambiguousCitationPages(
  citations: CitationRef[],
): Map<string, number> {
  const pagesByNode = new Map<number, Set<number>>();
  for (const citation of citations) {
    if (typeof citation.node_id !== "number") continue;
    if (typeof citation.page !== "number") continue;
    const pages = pagesByNode.get(citation.node_id) ?? new Set<number>();
    pages.add(citation.page);
    pagesByNode.set(citation.node_id, pages);
  }

  const shown = new Map<string, number>();
  for (const citation of citations) {
    const pages = pagesByNode.get(citation.node_id);
    if (pages && pages.size > 1) shown.set(citation.marker, citation.page);
  }
  return shown;
}
