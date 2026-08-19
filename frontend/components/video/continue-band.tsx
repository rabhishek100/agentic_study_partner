"use client";

import Link from "next/link";

import { formatSince } from "@/lib/video-activity";
import type { VideoConversationSummary } from "@/lib/video-types";

/** Enough to find your place, few enough to stay a shortcut and not a list. */
const SHOWN = 3;

/**
 * Where you left off, across every lecture.
 *
 * The library used to forget you between visits: it listed what you owned and
 * never that you had been in the middle of something. This is the same data
 * the workspace's own history panel uses, lifted one level up so it is visible
 * before you have chosen a lecture — which is when it is actually useful.
 *
 * Each entry deep-links to the conversation rather than the lecture. Landing
 * in an empty workspace beside a history panel is not continuing; it is being
 * asked to remember, which is the thing this removes.
 */
export function ContinueBand({
  conversations,
  now,
}: {
  conversations: VideoConversationSummary[];
  /** Injected so the elapsed time is testable without freezing the clock. */
  now?: Date;
}) {
  if (conversations.length === 0) return null;

  return (
    <nav aria-labelledby="continue-heading" className="flex flex-col gap-1">
      <p
        id="continue-heading"
        className="px-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        Continue
      </p>
      <ul className="flex flex-col gap-1">
        {conversations.slice(0, SHOWN).map((conversation) => (
          <li key={conversation.conversation_id}>
            <Link
              href={`/videos/${conversation.video_id}?conversation=${conversation.conversation_id}`}
              className="flex flex-col gap-1 rounded-md px-2 py-2 transition-colors hover:bg-surface-hover"
            >
              <span className="line-clamp-2 text-sm leading-snug">
                {conversation.title}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {conversation.video_title} ·{" "}
                {formatSince(conversation.updated_at, now)}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
