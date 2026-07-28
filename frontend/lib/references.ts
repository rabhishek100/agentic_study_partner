import { formatPath } from "./citations";
import type { CitationRef, EvidenceRef } from "./types";

/**
 * Shaping for the reference list.
 *
 * Real canonical paths are long and highly repetitive — five references from
 * one chapter all begin
 * "8 Tree-Based Methods :: 8.2 Bagging, Random Forests, Boosting, and
 * Bayesian Additive Regression Trees". Repeating that on every row buries the
 * one part that differs. These helpers lift the shared ancestry into a single
 * heading and separate what the answer actually cited from what retrieval
 * merely surfaced.
 */

export interface ReferenceGroup {
  bookId: number | null;
  bookTitle: string;
  /** Ancestry shared by every reference in the group; may be empty. */
  sharedPath: string[];
  items: EvidenceRef[];
}

/** The longest leading path shared by every one of `paths`. */
export function commonPathPrefix(paths: string[][]): string[] {
  if (paths.length === 0) return [];
  const first = paths[0]!;
  // A single reference still lifts its ancestry into the heading: canonical
  // paths run past a hundred characters, so leaving one inline just wraps the
  // row across three lines — the exact noise this is meant to remove.
  if (paths.length === 1) return first.slice(0, -1);

  const prefix: string[] = [];
  for (let depth = 0; depth < first.length; depth += 1) {
    const segment = first[depth];
    if (!paths.every((path) => path[depth] === segment)) break;
    // Never consume the whole path of any reference: every row must keep at
    // least its own leaf to be identifiable.
    if (paths.some((path) => path.length <= depth + 1)) break;
    prefix.push(segment!);
  }
  return prefix;
}

/** The part of `path` below `prefix`. */
export function pathBelow(path: string, prefix: string[]): string[] {
  const parts = formatPath(path);
  return parts.slice(prefix.length);
}

/** Group references by book, lifting each book's shared ancestry out. */
export function groupByBook(evidence: EvidenceRef[]): ReferenceGroup[] {
  const byBook = new Map<string, ReferenceGroup>();

  for (const reference of evidence) {
    const key = String(reference.book_id ?? "unknown");
    const existing = byBook.get(key);
    if (existing) {
      existing.items.push(reference);
    } else {
      byBook.set(key, {
        bookId: reference.book_id,
        bookTitle: reference.book_title ?? "This book",
        sharedPath: [],
        items: [reference],
      });
    }
  }

  return [...byBook.values()].map((group) => ({
    ...group,
    sharedPath: commonPathPrefix(group.items.map((item) => formatPath(item.path))),
  }));
}

/**
 * Split evidence into what the answer cited and what it did not.
 *
 * Retrieval returns a fixed number of candidates whether or not the model uses
 * them; presenting all of them as "sources" overstates what the answer rests
 * on. The uncited ones are still worth keeping — what retrieval surfaced and
 * the model ignored is diagnostic — so they are demoted, not discarded.
 */
export function partitionByCitation(
  evidence: EvidenceRef[],
  citations: CitationRef[],
): { cited: EvidenceRef[]; uncited: EvidenceRef[] } {
  if (citations.length === 0) {
    // A summary that cites nothing, or an abstention: treat every reference as
    // supporting rather than hiding the lot behind a disclosure.
    return { cited: evidence, uncited: [] };
  }

  const citedRanks = new Set(
    citations
      .map((citation) => citation.evidence_rank)
      .filter((rank): rank is number => rank != null),
  );
  const citedNodes = new Set(citations.map((citation) => citation.node_id));

  const cited: EvidenceRef[] = [];
  const uncited: EvidenceRef[] = [];
  for (const reference of evidence) {
    const isCited =
      (reference.rank != null && citedRanks.has(reference.rank)) ||
      citedNodes.has(reference.node_id);
    (isCited ? cited : uncited).push(reference);
  }
  return { cited, uncited };
}
