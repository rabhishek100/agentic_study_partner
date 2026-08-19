"use client";

import { MessageSquare } from "lucide-react";

import type { SideChatThread } from "@/lib/side-chat";
import { anchorLabel } from "@/lib/anchors";
import { cn } from "@/lib/utils";

export interface SessionQuestionsProps {
  threads: SideChatThread[];
  /** Which threads have a window on screen, so the list can say so. */
  openIds: Iterable<string>;
  onOpen: (thread: SideChatThread) => void;
  /** Where the reader is now, so the questions asked here can lead. */
  hereLabel?: string | null;
  footer?: React.ReactNode;
}

/**
 * Every question asked in this session, in the right region.
 *
 * A window closed thirty minutes ago is otherwise findable only through
 * conversation history, which is off this surface entirely — so a session that
 * runs long quietly loses the thing it was accumulating. This is the list that
 * keeps it, and it is a rendering of state that already exists: each entry is
 * a persisted side chat with an anchor that says where it points.
 *
 * It lives in the right region rather than in a rail of its own because the
 * region is already the one place this frame gives to material about the
 * canvas — evidence, the document, the transcript, and now this.
 */
export function SessionQuestions({
  threads,
  openIds,
  onOpen,
  hereLabel = null,
  footer,
}: SessionQuestionsProps) {
  const open = new Set(openIds);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-baseline gap-2 border-b border-divider px-4 py-3">
        <h2 className="text-eyebrow uppercase text-muted-foreground">Questions</h2>
        <span className="text-xs tabular-nums text-muted-foreground">
          {threads.length}
        </span>
        <span className="ml-auto text-xs text-muted-foreground">this session</span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {threads.length === 0 ? (
          <p className="px-2 py-4 text-sm leading-relaxed text-muted-foreground">
            Nothing asked yet. Questions you ask{" "}
            {hereLabel ? `from ${hereLabel} ` : ""}appear here, and stay with the
            source between visits.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {threads.map((thread) => {
              const label = thread.anchors[0]
                ? anchorLabel(thread.anchors[0])
                : null;
              const isHere = Boolean(hereLabel) && label === hereLabel;
              return (
                <li key={thread.conversation_id}>
                  <button
                    type="button"
                    onClick={() => onOpen(thread)}
                    aria-current={isHere ? "true" : undefined}
                    className={cn(
                      "flex w-full flex-col gap-1 rounded-md px-2 py-2 text-left",
                      "hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                      isHere &&
                        "border-l-[3px] border-primary bg-citation-muted pl-[5px]",
                    )}
                  >
                    <span className="flex items-center gap-2">
                      <span
                        className={cn(
                          "font-mono text-xs tabular-nums",
                          isHere ? "text-citation" : "text-muted-foreground",
                        )}
                      >
                        {label ?? "No anchor"}
                      </span>
                      {open.has(thread.conversation_id) && (
                        <span className="ml-auto inline-flex items-center gap-1 text-xs text-muted-foreground">
                          <MessageSquare aria-hidden className="size-3.5" />
                          open
                        </span>
                      )}
                    </span>
                    <span
                      className={cn(
                        "line-clamp-2 text-sm leading-snug",
                        isHere ? "text-foreground" : "text-muted-foreground",
                      )}
                    >
                      {thread.title}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {footer && (
        <div className="shrink-0 border-t border-divider p-3">{footer}</div>
      )}
    </div>
  );
}
