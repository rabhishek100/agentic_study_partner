"use client";

import { BookOpenText, ChevronRight } from "lucide-react";

import { formatPath } from "@/lib/citations";
import type { BookSummary, EvidenceRef } from "@/lib/types";

export function BookStudyContextBar({
  books,
  selectedBookIds,
  activeEvidence,
}: {
  books: BookSummary[];
  selectedBookIds: number[];
  activeEvidence: EvidenceRef | null;
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
      className="flex min-h-16 items-center gap-4 border-b border-border bg-card/45 px-4 py-2 md:px-7"
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
    </section>
  );
}
