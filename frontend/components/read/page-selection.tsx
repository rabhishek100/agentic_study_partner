"use client";

import { BookOpen, Check, Lightbulb, Loader2, MessageSquarePlus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Ignore a stray click-drag that selects a character or two. */
const MINIMUM_SELECTION_CHARS = 3;

/** Gap between the selection and the popover offered above it. */
const OFFSET_PX = 10;
const ESTIMATED_WIDTH_PX = 288;
const ESTIMATED_HEIGHT_PX = 150;

export interface PageSelection {
  text: string;
  /** Viewport coordinates for the popover, already clamped on screen. */
  x: number;
  y: number;
}

/**
 * Read the current selection, when it sits inside the rendered page.
 *
 * Mirrors `useSelectionToAsk` over answer text, and differs in what it
 * requires: a selection has to be inside pdf.js's text layer, because that is
 * the only part of this surface whose words belong to the book. A caption
 * drawn into a figure has no text layer and cannot be selected at all, which
 * is the same reason the server treats an unmatched selection as a designed
 * state rather than an error.
 *
 * Computed on pointer and key release rather than on every `selectionchange`,
 * or the popover would chase the cursor while the reader drags.
 */
export function usePageSelection(
  container: React.RefObject<HTMLElement | null>,
): { selected: PageSelection | null; dismiss: () => void } {
  const [selected, setSelected] = useState<PageSelection | null>(null);

  const dismiss = useCallback(() => setSelected(null), []);

  const read = useCallback(() => {
    const root = container.current;
    const selection = document.getSelection();
    if (!root || !selection || selection.isCollapsed || !selection.rangeCount) {
      setSelected(null);
      return;
    }
    const text = selection.toString().trim();
    if (text.length < MINIMUM_SELECTION_CHARS) {
      setSelected(null);
      return;
    }
    const range = selection.getRangeAt(0);
    if (!root.contains(range.commonAncestorContainer)) {
      setSelected(null);
      return;
    }
    const element =
      range.commonAncestorContainer instanceof Element
        ? range.commonAncestorContainer
        : range.commonAncestorContainer.parentElement;
    if (!element?.closest(".react-pdf__Page__textContent")) {
      setSelected(null);
      return;
    }

    const bounds = range.getBoundingClientRect();
    if (!bounds.width && !bounds.height) {
      setSelected(null);
      return;
    }
    const middle = bounds.left + bounds.width / 2;
    const above = bounds.top - OFFSET_PX - ESTIMATED_HEIGHT_PX;
    setSelected({
      text,
      x: Math.min(
        Math.max(middle, ESTIMATED_WIDTH_PX / 2 + 8),
        window.innerWidth - ESTIMATED_WIDTH_PX / 2 - 8,
      ),
      // Below the selection when there is no room above it.
      y: above > 8 ? above : bounds.bottom + OFFSET_PX,
    });
  }, [container]);

  useEffect(() => {
    const onSelectionChange = () => {
      const selection = document.getSelection();
      if (!selection || selection.isCollapsed) setSelected(null);
    };
    document.addEventListener("pointerup", read);
    document.addEventListener("keyup", read);
    document.addEventListener("selectionchange", onSelectionChange);
    return () => {
      document.removeEventListener("pointerup", read);
      document.removeEventListener("keyup", read);
      document.removeEventListener("selectionchange", onSelectionChange);
    };
  }, [read]);

  return { selected, dismiss };
}

interface Resolution {
  matched: boolean;
  label: string;
  passage_count: number;
}

/**
 * What the server made of this selection, fetched while the reader decides.
 *
 * Resolving before asking is the point: a reader learns whether their
 * highlight is citable *before* they commit a question to it. A miss is not an
 * error and does not block anything — it changes what the answer will rest on,
 * and saying so up front is the difference between an answer that quietly
 * grounds on the page and one the reader knows grounds on the page.
 */
function useResolution(
  sessionId: string | null,
  bookId: number,
  page: number,
  text: string | null,
): { resolution: Resolution | null; isResolving: boolean } {
  const [resolution, setResolution] = useState<Resolution | null>(null);
  const [isResolving, setIsResolving] = useState(false);

  useEffect(() => {
    if (!sessionId || !text) {
      setResolution(null);
      return;
    }
    let cancelled = false;
    setIsResolving(true);
    setResolution(null);
    apiFetch<Resolution>(`/reading-sessions/${sessionId}/anchors/resolve`, {
      method: "POST",
      body: JSON.stringify({
        anchor: {
          kind: "document_passage",
          book_id: bookId,
          page,
          selected_text: text,
        },
      }),
    })
      .then((resolved) => {
        if (!cancelled) setResolution(resolved);
      })
      // A preview that fails to load is a missing sentence, not a broken
      // gesture: the reader can still ask, and the answer resolves the anchor
      // again on its own terms.
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setIsResolving(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, bookId, page, text]);

  return { resolution, isResolving };
}

export interface PageSelectionPopoverProps {
  container: React.RefObject<HTMLElement | null>;
  sessionId: string | null;
  bookId: number;
  page: number;
  /** `question` is absent for "ask about this", which opens an empty window. */
  onAsk: (selectedText: string, question?: string) => void;
}

const PRESETS = [
  {
    key: "explain",
    label: "Explain in simpler terms",
    icon: Lightbulb,
    question: "Explain this passage in simpler terms.",
  },
  {
    key: "define",
    label: "Define the terms",
    icon: BookOpen,
    question: "Define the technical terms used in this passage.",
  },
] as const;

/** The offer that appears over a highlighted passage of the book. */
export function PageSelectionPopover({
  container,
  sessionId,
  bookId,
  page,
  onAsk,
}: PageSelectionPopoverProps) {
  const { selected, dismiss } = usePageSelection(container);
  const { resolution, isResolving } = useResolution(
    sessionId,
    bookId,
    page,
    selected?.text ?? null,
  );

  useEffect(() => {
    if (!selected) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [selected, dismiss]);

  if (!selected) return null;

  const take = (question?: string) => {
    onAsk(selected.text, question);
    document.getSelection()?.removeAllRanges();
    dismiss();
  };

  return (
    <div
      role="dialog"
      aria-label="Ask about the selected passage"
      // The pointer goes down here while the selection still exists; preventing
      // default keeps the browser from clearing it before the click lands.
      onPointerDown={(event) => event.preventDefault()}
      style={{ left: selected.x, top: selected.y, width: ESTIMATED_WIDTH_PX }}
      className="fixed z-drawer -translate-x-1/2 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg motion-safe:animate-in motion-safe:fade-in"
    >
      <button
        type="button"
        onClick={() => take()}
        className="flex h-8 w-full items-center gap-3 rounded-sm px-2 text-sm font-medium hover:bg-accent focus-visible:bg-accent focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
      >
        <MessageSquarePlus aria-hidden className="size-4 text-primary" />
        Ask about this
      </button>
      {PRESETS.map((preset) => (
        <button
          key={preset.key}
          type="button"
          onClick={() => take(preset.question)}
          className="flex h-8 w-full items-center gap-3 rounded-sm px-2 text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-ring"
        >
          <preset.icon aria-hidden className="size-4 text-muted-foreground" />
          {preset.label}
        </button>
      ))}

      <p
        aria-live="polite"
        className={cn(
          "mt-1 flex items-start gap-2 border-t border-border px-2 py-2 text-xs leading-snug",
          resolution && !resolution.matched
            ? "text-warning"
            : "text-muted-foreground",
        )}
      >
        {isResolving ? (
          <>
            <Loader2 aria-hidden className="mt-1 size-3.5 shrink-0 animate-spin" />
            Checking this against the book…
          </>
        ) : resolution?.matched ? (
          <>
            <Check aria-hidden className="mt-1 size-3.5 shrink-0 text-citation" />
            <span>
              Matches {resolution.label} — {resolution.passage_count}{" "}
              {resolution.passage_count === 1 ? "passage" : "passages"}. The
              answer can cite them.
            </span>
          </>
        ) : resolution ? (
          <span>
            No match in the book&apos;s text. The words go in as context, and the
            answer rests on {resolution.label} instead.
          </span>
        ) : (
          <span>Ask about this passage.</span>
        )}
      </p>
    </div>
  );
}
