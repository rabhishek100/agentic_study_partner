/**
 * What the reader was last doing, per lecture.
 *
 * The library knew when a lecture arrived and nothing about whether it had
 * ever been used, so every visit started cold: remember which lecture, open
 * it, find where you were. The conversations endpoint has held the answer all
 * along — this is the arithmetic that turns it into an ordering and a line of
 * text.
 */

import type { VideoConversationSummary, VideoSummary } from "@/lib/video-types";

/**
 * The most recent conversation for each lecture.
 *
 * The server already orders by `updated_at desc`, but the map is built by
 * comparing timestamps rather than by trusting arrival order: a caller that
 * concatenates two pages, or a server that changes its mind about ordering,
 * would otherwise silently pin the wrong conversation to a lecture.
 */
export function latestByVideo(
  conversations: VideoConversationSummary[],
): Map<string, VideoConversationSummary> {
  const latest = new Map<string, VideoConversationSummary>();
  for (const conversation of conversations) {
    const held = latest.get(conversation.video_id);
    if (!held || conversation.updated_at > held.updated_at) {
      latest.set(conversation.video_id, conversation);
    }
  }
  return latest;
}

/**
 * How long ago, in the shortest form that is still exact enough to act on.
 *
 * Minutes and hours while the session is plausibly still in mind, then days,
 * then the date — past a week "9 days ago" is arithmetic the reader has to do
 * in reverse to place it against anything else they remember.
 */
export function formatSince(iso: string, now: Date = new Date()): string {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const seconds = Math.max(0, (now.getTime() - then.getTime()) / 1000);
  if (seconds < 90) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return then.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    ...(then.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }),
  });
}

/**
 * Lectures in the order the reader is likely to want them.
 *
 * Anything with a conversation comes first, most recently asked at the front;
 * everything else follows by when it arrived. Recency of *use* beats recency
 * of acquisition here — a lecture added this morning and never opened is not
 * more wanted than the one being worked through all week.
 */
export function sortByActivity(
  videos: VideoSummary[],
  latest: Map<string, VideoConversationSummary>,
): VideoSummary[] {
  return [...videos].sort((left, right) => {
    const leftAsked = latest.get(left.video_id)?.updated_at;
    const rightAsked = latest.get(right.video_id)?.updated_at;
    if (leftAsked && rightAsked) return rightAsked.localeCompare(leftAsked);
    if (leftAsked) return -1;
    if (rightAsked) return 1;
    return right.created_at.localeCompare(left.created_at);
  });
}

/** Whether a search box would be earning its space. */
export const SEARCH_THRESHOLD = 8;

/**
 * Title search, on words rather than on the exact string.
 *
 * Two kinds of title share this box. Uploads are filenames
 * (`cme295-lecture1-h264.mp4`) and lectures are prose (`CME 295 — Transformers
 * & LLMs, Lecture 1`), so the same query has to reach across a separator in one
 * and a space in the other: "cme295 lecture" is a reasonable thing to type and
 * should find both. Every token has to appear, but the punctuation between them
 * does not have to match — which is also what lets "learning bias" find
 * "Statistical Learning — Bias and Variance".
 */
function squash(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

export function matchesQuery(video: VideoSummary, query: string): boolean {
  const tokens = query.split(/\s+/).map(squash).filter(Boolean);
  if (tokens.length === 0) return true;
  const title = squash(video.title);
  return tokens.every((token) => title.includes(token));
}
