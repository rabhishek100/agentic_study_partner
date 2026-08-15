"use client";

import {
  BookOpenText,
  ChevronDown,
  ChevronRight,
  Library,
  MessagesSquare,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatPath } from "@/lib/citations";
import type { BookSummary, EvidenceRef } from "@/lib/types";

export function BookStudyContextBar({
  books,
  selectedBookIds,
  activeEvidence,
  conversationCount,
  onOpenLibrary,
}: {
  books: BookSummary[];
  selectedBookIds: number[];
  activeEvidence: EvidenceRef | null;
  conversationCount: number;
  onOpenLibrary: () => void;
}) {
  const selected = books.filter((book) => selectedBookIds.includes(book.book_id));
  const primary =
    selected.find((book) => book.book_id === activeEvidence?.book_id) ??
    selected[0] ??
    books[0] ??
    null;
  const path = activeEvidence ? formatPath(activeEvidence.path) : [];
  const chapter = path.at(-1) ?? "Your grounded study workspace";
  const chapterLabel = path.length > 1 ? path.at(-2) : "Current scope";
  const selectionLabel = primary?.title ?? "Choose a book";

  return (
    <section
      aria-label="Study context"
      className="flex min-h-20 items-center gap-4 border-b border-border bg-card/45 px-4 py-3 md:px-7"
    >
      <div className="flex min-w-0 flex-1 items-center gap-3 xl:w-[360px] xl:flex-none">
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-positive-muted text-positive">
          <BookOpenText className="size-5" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="text-[0.68rem] font-medium text-muted-foreground">Book</p>
          <p className="truncate text-sm font-medium" title={selectionLabel}>
            {selectionLabel}
          </p>
          {primary?.author ? (
            <p className="truncate text-xs text-positive">by {primary.author}</p>
          ) : null}
        </div>
      </div>

      <ChevronRight className="hidden size-4 shrink-0 text-muted-foreground md:block" aria-hidden />

      <div className="hidden min-w-0 flex-1 md:block xl:w-[280px] xl:flex-none">
        <p className="text-[0.68rem] font-medium text-muted-foreground">
          {chapterLabel}
        </p>
        <p className="truncate text-sm font-medium" title={chapter}>
          {chapter}
        </p>
      </div>

      <div className="ml-auto flex shrink-0 items-center gap-2 xl:ml-0">
        <Button
          variant="outline"
          size="lg"
          aria-label="Change book"
          onClick={onOpenLibrary}
        >
          <Library className="size-4" aria-hidden />
          <span className="hidden lg:inline">Change book</span>
          <ChevronDown className="hidden size-3.5 opacity-60 lg:block" aria-hidden />
        </Button>
        <Button
          variant="outline"
          size="lg"
          aria-label="Open conversations"
          onClick={onOpenLibrary}
        >
          <MessagesSquare className="size-4" aria-hidden />
          <span className="hidden lg:inline">Conversations</span>
          {conversationCount > 0 ? (
            <span className="rounded bg-muted px-1.5 font-mono text-[0.65rem] text-muted-foreground">
              {conversationCount}
            </span>
          ) : null}
        </Button>
      </div>
    </section>
  );
}
