"use client";

import { ArrowLeft, Lock, MoreHorizontal, PictureInPicture2 } from "lucide-react";
import { useLayoutEffect, useRef } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { SideChatThread } from "@/lib/side-chat";
import { anchorLabel, anchorText } from "@/lib/anchors";
import { cn } from "@/lib/utils";

export interface SessionQuestionsProps {
  threads: SideChatThread[];
  /** Which threads float in a window of their own, so the list can say so. */
  detachedIds: Iterable<string>;
  /** The thread showing in this panel, or null while the list is showing. */
  selectedId: string | null;
  onOpen: (thread: SideChatThread) => void;
  onBack: () => void;
  /**
   * Pop the shown thread into a window. Absent where floating is impossible —
   * a narrow viewport has no room for it, and the panel is the better home.
   */
  onDetach?: (thread: SideChatThread) => void;
  /**
   * Every attached thread, mounted, with all but the shown one hidden.
   *
   * Passed in rather than rendered here because the threads are the caller's:
   * a lecture answer seeks a player, a book answer opens a page, and the panel
   * knows about neither. What the panel guarantees is that they all stay
   * mounted, so an answer still streaming survives going back to the list.
   */
  detail?: React.ReactNode;
  /** Where the reader is now, so the questions asked here can lead. */
  hereLabel?: string | null;
  /** Session-wide actions — the recap, the handoff, the lock. */
  menu?: React.ReactNode;
  /**
   * Said on the surface when the reader has locked the session to its source.
   *
   * The lock is a standing instruction that survives the visit, and it produces
   * silent refusals: an answer that simply says the source does not cover this.
   * A reader who cannot see that they set it reads those refusals as bad
   * retrieval, so the control may live in the menu but its engaged state may
   * not.
   */
  lockLabel?: string | null;
  footer?: React.ReactNode;
}

/**
 * Every question asked in this session, and the one the reader is reading.
 *
 * Both live in this one column, and only one shows at a time. That is the
 * whole design: a thread was previously drawn three times over — a row here, a
 * floating window, and a chip in a dock at the foot of the screen — with
 * nothing to say which of the three was the real one. Here a thread has one
 * home, its row, and opening it puts it in the same place the list was.
 *
 * Neither view is unmounted when it goes away. The list keeps its scroll
 * position and its half-typed question; the threads keep their streams, which
 * is the property the floating windows were built around and the reason going
 * back cannot be allowed to tear one down.
 */
export function SessionQuestions({
  threads,
  detachedIds,
  selectedId,
  onOpen,
  onBack,
  onDetach,
  detail,
  hereLabel = null,
  menu,
  lockLabel = null,
  footer,
}: SessionQuestionsProps) {
  const detached = new Set(detachedIds);
  const selected =
    threads.find((thread) => thread.conversation_id === selectedId) ?? null;
  const showing = Boolean(selected);

  const rowsRef = useRef(new Map<string, HTMLButtonElement | null>());
  const threadRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const previousRef = useRef<string | null>(null);

  /**
   * Move focus with the view.
   *
   * Nothing does this for us: the swap is a re-render inside one container, not
   * a navigation. Left alone, hiding the view that holds the focused element
   * drops focus to `body`, and the next Tab restarts at the top of the document
   * — a hundred stops from where the reader was.
   *
   * Opening focuses the thread's own container rather than the back button or
   * the composer, so what is announced is the question that just opened. Going
   * back returns to the row that opened it, which is where the reader's eye
   * already is.
   */
  useLayoutEffect(() => {
    const previous = previousRef.current;
    previousRef.current = selectedId;
    if (previous === selectedId) return;
    if (selectedId) {
      threadRef.current?.focus();
      return;
    }
    if (!previous) return;
    const row = rowsRef.current.get(previous);
    if (row) {
      row.focus();
      row.scrollIntoView({ block: "nearest" });
    } else {
      listRef.current?.focus();
    }
  }, [selectedId]);

  const status = selected
    ? `Showing the question: ${selected.title}`
    : `Questions in this session: ${threads.length}`;

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      {/* Mounted across both views, so it is never the thing that just appeared. */}
      <p className="sr-only" role="status" aria-live="polite">
        {status}
      </p>

      <div
        ref={listRef}
        tabIndex={-1}
        hidden={showing}
        className={cn(
          "flex min-h-0 flex-1 flex-col outline-none",
          showing && "hidden",
        )}
      >
        <header className="flex shrink-0 items-center gap-2 border-b border-divider px-4 py-2">
          <h2 className="text-eyebrow uppercase text-muted-foreground">
            Questions
          </h2>
          <span className="text-xs tabular-nums text-muted-foreground">
            {threads.length}
          </span>
          {lockLabel && (
            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
              <Lock aria-hidden className="size-3.5" />
              {lockLabel}
            </span>
          )}
          {menu && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  className="ml-auto"
                  aria-label="Session actions"
                >
                  <MoreHorizontal aria-hidden />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-72">
                {menu}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-2 [scrollbar-gutter:stable]">
          {threads.length === 0 ? (
            <p className="px-2 py-4 text-sm leading-relaxed text-muted-foreground">
              Nothing asked yet. Questions you ask{" "}
              {hereLabel ? `from ${hereLabel} ` : ""}appear here, and stay with
              the source between visits.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {threads.map((thread) => {
                const anchor = thread.anchors[0];
                const label = anchor ? anchorLabel(anchor) : null;
                const quote = anchor ? anchorText(anchor) : null;
                const isHere = Boolean(hereLabel) && label === hereLabel;
                const isDetached = detached.has(thread.conversation_id);
                return (
                  <li key={thread.conversation_id}>
                    <button
                      type="button"
                      ref={(node) => {
                        rowsRef.current.set(thread.conversation_id, node);
                      }}
                      onClick={() => onOpen(thread)}
                      aria-current={isHere ? "true" : undefined}
                      // Built rather than left to concatenation: read in source
                      // order the row announces "p. 21 detached explain the
                      // diagram", which buries the one thing that tells two
                      // rows apart.
                      aria-label={[
                        thread.title,
                        label ? `Anchored to ${spoken(label)}` : "No anchor",
                        isDetached ? "In a floating window" : null,
                      ]
                        .filter(Boolean)
                        .join(". ")}
                      className={cn(
                        "flex w-full flex-col gap-1 rounded-md px-2 py-2 text-left",
                        "hover:bg-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                        isHere &&
                          "border-s-[3px] border-primary bg-citation-muted ps-[5px]",
                      )}
                    >
                      <span className="flex items-center gap-2">
                        <span
                          dir="ltr"
                          className={cn(
                            "min-w-0 truncate font-mono text-xs tabular-nums [unicode-bidi:isolate]",
                            isHere ? "text-citation" : "text-muted-foreground",
                          )}
                        >
                          {quote ? `“${quote}”` : (label ?? "No anchor")}
                        </span>
                        {isDetached && (
                          <span
                            aria-hidden
                            className="ms-auto inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground"
                          >
                            <PictureInPicture2 className="size-3.5" />
                            Detached
                          </span>
                        )}
                      </span>
                      <span
                        className={cn(
                          "line-clamp-2 font-serif text-sm leading-snug",
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

      <div
        ref={threadRef}
        tabIndex={-1}
        hidden={!showing}
        aria-label={selected ? `Question: ${selected.title}` : undefined}
        onKeyDown={(event) => {
          // Escape leaves the thread, unless something inside it — the anchor
          // editor's menus, a composer mid-word — wants the key first.
          if (event.key !== "Escape" || event.defaultPrevented) return;
          event.stopPropagation();
          onBack();
        }}
        className={cn(
          "flex min-h-0 flex-1 flex-col outline-none",
          !showing && "hidden",
        )}
      >
        <header className="flex shrink-0 items-center gap-1 border-b border-divider py-1 pe-1 ps-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Back to the questions in this session"
            onClick={onBack}
          >
            <ArrowLeft aria-hidden />
          </Button>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">
              {selected?.title}
            </span>
            {selected?.anchors[0] && (
              <span
                dir="ltr"
                className="block font-mono text-xs tabular-nums text-muted-foreground [unicode-bidi:isolate]"
              >
                {anchorLabel(selected.anchors[0])}
              </span>
            )}
          </span>
          {onDetach && selected && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`Open “${selected.title}” in a floating window`}
              onClick={() => onDetach(selected)}
            >
              <PictureInPicture2 aria-hidden />
            </Button>
          )}
        </header>

        <div className="flex min-h-0 flex-1 flex-col">{detail}</div>
      </div>
    </div>
  );
}

/** "p. 21" reads as the letter p; a timecode reads well enough as it stands. */
function spoken(label: string): string {
  return label.startsWith("p. ") ? `page ${label.slice(3)}` : label;
}
