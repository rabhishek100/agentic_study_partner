"use client";

import { cn } from "@/lib/utils";
import type { SideChatThread } from "@/lib/side-chat";
import type { Anchor } from "@/lib/types";

/** The page an anchored question names, or null when it names none. */
export function anchoredPage(anchors: Anchor[]): number | null {
  for (const anchor of anchors) {
    if (anchor.kind === "document_page" || anchor.kind === "document_passage") {
      return anchor.page;
    }
  }
  return null;
}

export interface Mark {
  thread: SideChatThread;
  page: number;
}

/** Every anchored question in this session, in the order they were asked. */
export function marksFor(threads: SideChatThread[]): Mark[] {
  return threads.flatMap((thread) => {
    const page = anchoredPage(thread.anchors);
    return page === null ? [] : [{ thread, page }];
  });
}

export interface MarginMarksProps {
  marks: Mark[];
  page: number;
  onOpen: (thread: SideChatThread) => void;
  onGoToPage: (page: number) => void;
}

/**
 * The questions this reader has left in the margins.
 *
 * The accumulated artifact is what separates source-first study from asking
 * about a book: after an hour, an ask-first session has a transcript and this
 * one has a marked-up book. Every mark is a persisted side chat, so they
 * survive the visit that made them without anything else being stored.
 *
 * The marks are listed in a gutter rather than pinned beside the sentence they
 * name. Aligning them to the passage means locating the anchored text in
 * pdf.js's text layer on every render — the citation highlighter already does
 * that for one excerpt, and doing it for every mark belongs with that code
 * rather than here. The gutter is honest about position without pretending to
 * a precision it does not have.
 */
export function MarginMarks({
  marks,
  page,
  onOpen,
  onGoToPage,
}: MarginMarksProps) {
  const here = marks.filter((mark) => mark.page === page);
  const elsewhere = marks.filter((mark) => mark.page !== page);

  if (marks.length === 0) return null;

  return (
    <nav
      aria-label="Questions you asked in this book"
      className="flex w-44 shrink-0 flex-col gap-2 overflow-y-auto border-r border-divider px-2 py-3"
    >
      {here.length > 0 && (
        <ul className="flex flex-col gap-2">
          {here.map((mark) => (
            <li key={mark.thread.conversation_id}>
              <button
                type="button"
                onClick={() => onOpen(mark.thread)}
                className={cn(
                  "flex w-full gap-2 rounded-md border-l-[3px] border-primary bg-citation-muted px-2 py-2 text-left",
                  "hover:bg-wash focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                )}
              >
                <span className="line-clamp-3 text-xs leading-snug text-citation">
                  {mark.thread.title}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {here.length === 0 && (
        <p className="px-1 text-xs leading-snug text-muted-foreground">
          Nothing asked on this page yet.
        </p>
      )}

      {elsewhere.length > 0 && (
        <>
          <p className="mt-2 px-1 text-eyebrow uppercase text-muted-foreground">
            Elsewhere
          </p>
          <ul className="flex flex-col gap-1">
            {elsewhere.map((mark) => (
              <li key={mark.thread.conversation_id}>
                <button
                  type="button"
                  onClick={() => onGoToPage(mark.page)}
                  className="flex w-full items-baseline gap-2 rounded-md px-2 py-2 text-left hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                    {mark.page}
                  </span>
                  <span className="line-clamp-2 text-xs leading-snug text-muted-foreground">
                    {mark.thread.title}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </nav>
  );
}
