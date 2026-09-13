import { apiFetch } from "./api";
import type { PassageResponse, PassageSegment, ReadingRef } from "./types";

export function passagePath(reading: ReadingRef, offset: number): string {
  const query = new URLSearchParams({ offset: String(offset) });
  if (reading.node_id != null) query.set("node_id", String(reading.node_id));
  return `/books/${reading.book_id}/passage?${query}`;
}

/** Fetch the complete canonical scope for narration without expanding the DOM. */
export async function fetchCompletePassage(
  reading: ReadingRef,
  signal?: AbortSignal,
): Promise<PassageSegment[]> {
  const segments: PassageSegment[] = [];
  const visited = new Set<number>();
  let offset: number | null = 0;

  while (offset !== null) {
    if (visited.has(offset)) throw new Error("The chapter returned a repeated continuation point.");
    visited.add(offset);
    const response: PassageResponse = await apiFetch<PassageResponse>(
      passagePath(reading, offset),
      { signal },
    );
    segments.push(...response.segments);
    offset = response.next_offset;
  }
  return segments;
}
