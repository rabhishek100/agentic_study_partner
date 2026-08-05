import type { VideoCitationRef, VideoEvidenceRef } from "./video-types";

/**
 * Shaping for a lecture's reference list.
 *
 * A book groups references by book and lifts the shared chapter path out of
 * every row. A lecture has no hierarchy to lift, but it does have something a
 * book does not: one question is answered from three genuinely different kinds
 * of source. What the speaker said, what was on screen, and what a linked deck
 * says are not interchangeable, and flattening them into one list is exactly
 * the conflation the grounding prompt spends four rules preventing.
 */

export type VideoSourceKind = "lecture" | "document";

export interface VideoReferenceGroup {
  key: string;
  kind: VideoSourceKind;
  /** "This lecture", or the linked document's title. */
  title: string;
  /** Present only for a document group, so a row can open it. */
  resourceId: string | null;
  items: VideoEvidenceRef[];
}

/**
 * Split evidence into what the answer cited and what it did not.
 *
 * Retrieval returns a fixed number of candidates whether or not the model uses
 * them; presenting all of them as "sources" overstates what the answer rests
 * on. The uncited ones stay available — what retrieval surfaced and the model
 * ignored is diagnostic — so they are demoted rather than discarded.
 */
export function partitionByCitation(
  evidence: VideoEvidenceRef[],
  citations: VideoCitationRef[],
): { cited: VideoEvidenceRef[]; uncited: VideoEvidenceRef[] } {
  if (citations.length === 0) {
    // An abstention, or a clarification: nothing is being claimed, so there is
    // no "cited" subset to separate out.
    return { cited: evidence, uncited: [] };
  }
  const citedRanks = new Set(
    citations.map((citation) => citation.evidence_rank),
  );
  const cited: VideoEvidenceRef[] = [];
  const uncited: VideoEvidenceRef[] = [];
  for (const item of evidence) {
    (citedRanks.has(item.rank) ? cited : uncited).push(item);
  }
  return { cited, uncited };
}

/**
 * Group evidence by the source it actually came from.
 *
 * The lecture is one group whatever modality reached it, because a frame and
 * the sentence spoken over it are the same recording at the same moment. Each
 * linked document is its own group, because a page is a different place with
 * its own stable reference.
 */
export function groupBySource(
  evidence: VideoEvidenceRef[],
): VideoReferenceGroup[] {
  const groups = new Map<string, VideoReferenceGroup>();
  for (const item of evidence) {
    const isDocument = item.modality === "resource_page";
    // A document page with no resource id cannot be opened, but it is still a
    // document rather than the recording, so it keeps its own group.
    const key = isDocument
      ? `document:${item.resource_id ?? item.resource_title ?? "unknown"}`
      : "lecture";
    const existing = groups.get(key);
    if (existing) {
      existing.items.push(item);
      continue;
    }
    groups.set(key, {
      key,
      kind: isDocument ? "document" : "lecture",
      title: isDocument ? (item.resource_title ?? "Linked document") : "This lecture",
      resourceId: isDocument ? item.resource_id : null,
      items: [item],
    });
  }
  // The recording first when it is present: it is what the reader is looking
  // at, and a deck is the supporting reference beside it.
  return [...groups.values()].sort((left, right) =>
    left.kind === right.kind ? 0 : left.kind === "lecture" ? -1 : 1,
  );
}
