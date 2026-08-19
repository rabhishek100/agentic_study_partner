"use client";

import { timecode } from "@/lib/timecode";
import type { SideChatThread } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The moment an anchored question names, or null when it names none. */
export function anchoredMoment(anchors: Anchor[]): number | null {
  for (const anchor of anchors) {
    if (anchor.kind === "lecture_moment") return anchor.timestamp_ms;
    if (anchor.kind === "lecture_stretch") return anchor.start_ms;
  }
  return null;
}

export interface MomentMark {
  thread: SideChatThread;
  atMs: number;
}

/** Every anchored question in this session, in lecture order. */
export function momentMarksFor(threads: SideChatThread[]): MomentMark[] {
  return threads
    .flatMap((thread) => {
      const atMs = anchoredMoment(thread.anchors);
      return atMs === null ? [] : [{ thread, atMs }];
    })
    .sort((left, right) => left.atMs - right.atMs);
}

/** The mark nearest the playhead, which is the one "here" means. */
export function nearestMark(marks: MomentMark[], atMs: number): MomentMark | null {
  let nearest: MomentMark | null = null;
  let distance = Infinity;
  for (const mark of marks) {
    const gap = Math.abs(mark.atMs - atMs);
    if (gap < distance) {
      nearest = mark;
      distance = gap;
    }
  }
  return nearest;
}

export interface MomentMarksProps {
  marks: MomentMark[];
  atMs: number;
  onOpen: (thread: SideChatThread) => void;
  onSeek: (milliseconds: number) => void;
}

/**
 * The questions this viewer has left along the lecture.
 *
 * Deliberately not the reading surface's `MarginMarks`, despite doing the same
 * job. There, "here" is exact — a mark is on this page or it is not — so the
 * list splits in two and the current page's marks come first. A lecture has no
 * such boundary: time is continuous, every mark is on the same timeline, and
 * the useful ordering is the lecture's own. Sharing one component would mean a
 * parameter that turns one of those behaviours into the other, which is two
 * components wearing a trench coat.
 */
export function MomentMarks({ marks, atMs, onOpen, onSeek }: MomentMarksProps) {
  if (marks.length === 0) return null;
  const nearest = nearestMark(marks, atMs);

  return (
    <nav
      aria-label="Questions you asked in this lecture"
      className="flex w-44 shrink-0 flex-col gap-1 overflow-y-auto border-r border-divider px-2 py-3"
    >
      {marks.map((mark) => {
        const active = mark.thread.conversation_id === nearest?.thread.conversation_id;
        return (
          <button
            key={mark.thread.conversation_id}
            type="button"
            aria-current={active ? "true" : undefined}
            onClick={() => {
              onSeek(mark.atMs);
              onOpen(mark.thread);
            }}
            className={cn(
              "flex w-full flex-col gap-1 rounded-md px-2 py-2 text-left",
              "hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
              active &&
                "border-l-[3px] border-primary bg-citation-muted pl-[5px] hover:bg-wash",
            )}
          >
            <span
              className={cn(
                "font-mono text-xs tabular-nums",
                active ? "text-citation" : "text-muted-foreground",
              )}
            >
              {timecode(mark.atMs)}
            </span>
            <span
              className={cn(
                "line-clamp-3 text-xs leading-snug",
                active ? "text-citation" : "text-muted-foreground",
              )}
            >
              {mark.thread.title}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
