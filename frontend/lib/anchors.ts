/**
 * How an anchor reads on screen.
 *
 * Two kinds of anchor point at two different things and are legible in
 * different ways. A quote is text the reader highlighted in an answer, so its
 * words *are* the label. A source anchor is a place — a page, a section — and a
 * page anchor has no words at all: the reader made no selection, they were
 * simply there. Rendering the second as though it were the first is how a
 * window ends up titled "undefined".
 */

import { timecode } from "@/lib/timecode";
import type { Anchor } from "@/lib/types";

/** Where the anchor points, in the terms the reader would use. */
export function anchorLabel(anchor: Anchor): string {
  switch (anchor.kind) {
    case "document_page":
    case "document_passage":
      return `p. ${anchor.page}`;
    case "lecture_moment":
      return timecode(anchor.timestamp_ms);
    case "lecture_stretch":
      return `${timecode(anchor.start_ms)} – ${timecode(anchor.end_ms)}`;
    default:
      return "From an answer";
  }
}

/**
 * The words this anchor carries, or null when it carries none.
 *
 * Null is a real answer rather than an empty string: a page anchor has nothing
 * to quote, and a chip that renders an empty blockquote for it looks like text
 * that failed to load.
 */
export function anchorText(anchor: Anchor): string | null {
  switch (anchor.kind) {
    case "document_passage":
      return anchor.selected_text;
    case "document_page":
    case "lecture_moment":
    case "lecture_stretch":
      // A place, not a selection. The viewer marked time; the words are the
      // transcript units the answer will cite, so there is nothing separate
      // to quote and nothing that could fail to match.
      return null;
    default:
      return anchor.quoted_text;
  }
}

/** Whether the reader can detach this anchor from its window. */
export function isRemovable(anchor: Anchor): boolean {
  // A source anchor is what the window is *about* — remove it and the thread
  // is a question about a page it no longer names. A quote can be detached
  // because a window can carry several and the reader curates them.
  return anchor.kind === undefined || anchor.kind === "answer_quote";
}

/**
 * The moment in a lecture an anchored question names, or null.
 *
 * Kept when the timeline gutter went, because clicking a question about 12:04
 * while the lecture sits at 40:00 shows an answer with no picture behind it.
 * The list opens the thread; this is what lets the caller move the playhead
 * with it.
 */
export function anchoredMoment(anchors: Anchor[]): number | null {
  for (const anchor of anchors) {
    if (anchor.kind === "lecture_moment") return anchor.timestamp_ms;
    if (anchor.kind === "lecture_stretch") return anchor.start_ms;
  }
  return null;
}
