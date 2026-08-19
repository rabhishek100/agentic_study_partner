"use client";

import { BookOpen } from "lucide-react";
import Link from "next/link";

import type { BookSummary } from "@/lib/types";

/**
 * The books in this library, offered to read.
 *
 * This exists because the first version did not. Reading was reachable only
 * through the scope selector — a popover whose job is choosing what to search
 * — behind a hover-revealed icon with no label. Four steps and invisible until
 * hovered is not an entry point; a reader who did not already know the feature
 * existed could not find it, and did not.
 *
 * Scope selection and navigation are different jobs. This is the second one.
 */
export function ReadableSources({
  books,
  noun,
}: {
  books: BookSummary[];
  /** "book" or "paper", so the heading names what the reader actually has. */
  noun: "book" | "paper";
}) {
  const readable = books.filter((book) => book.ready_at !== null);
  if (readable.length === 0) return null;

  return (
    <nav aria-labelledby="read-heading" className="flex flex-col gap-1">
      <p
        id="read-heading"
        className="px-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground"
      >
        {noun === "paper" ? "Read a paper" : "Read a book"}
      </p>
      <ul className="flex flex-col gap-1">
        {readable.map((book) => (
          <li key={book.book_id}>
            <Link
              href={`/read/${book.book_id}`}
              className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-surface-hover"
            >
              <BookOpen
                aria-hidden
                className="size-4 shrink-0 text-muted-foreground"
              />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm leading-snug">
                  {book.title}
                </span>
                {book.author && (
                  <span className="block truncate text-xs text-muted-foreground">
                    {book.author}
                  </span>
                )}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
