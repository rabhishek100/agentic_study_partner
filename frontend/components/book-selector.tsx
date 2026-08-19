"use client";

import { BookOpen, Check, ChevronsUpDown, Library, Pencil } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import type { BookSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * How the current selection reads in one line.
 *
 * The noun is a parameter because this component serves two libraries. The
 * papers route said "All 31 books" under a heading reading "Your paper
 * library" — the rail knew which it was and nothing else did.
 */
export function describeSelection(
  books: BookSummary[],
  selected: number[],
  noun: DocumentNoun = "book",
): string {
  const one = noun;
  const many = `${noun}s`;
  if (books.length === 0) return `No ${many}`;
  if (selected.length === 0) return `No ${many} selected`;
  if (selected.length === books.length) {
    return books.length === 1
      ? (books[0]?.title ?? `1 ${one}`)
      : `All ${books.length} ${many}`;
  }
  if (selected.length === 1) {
    const only = books.find((book) => book.book_id === selected[0]);
    return only?.title ?? `1 ${one}`;
  }
  return `${selected.length} of ${books.length} ${many}`;
}

/** Which library this is, so the copy can name it. */
export type DocumentNoun = "book" | "paper";

export interface BookSelectorProps {
  books: BookSummary[];
  selected: number[];
  onChange: (bookIds: number[]) => void;
  /** True once the conversation has turns that were answered under `selected`. */
  hasConversation: boolean;
  noun?: DocumentNoun;
  /**
   * Rename one document. Optional: where it is absent the list is a selector
   * and nothing else, which is what the mobile drawer and the tests want.
   */
  onRename?: (bookId: number, title: string) => Promise<void>;
}

/**
 * One row, mid-rename.
 *
 * A form rather than an input with handlers: Enter submits because that is
 * what a form does, which also means the control keeps working for anyone
 * driving it from the keyboard alone. Escape leaves without saving, because a
 * rename abandoned halfway should not be a rename.
 */
function RenameRow({
  title,
  busy,
  onSave,
  onCancel,
}: {
  title: string;
  busy: boolean;
  onSave: (title: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(title);
  const unchanged = draft.trim() === title.trim();

  return (
    <form
      className="flex items-center gap-2 px-2 py-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!draft.trim() || unchanged) return onCancel();
        onSave(draft.trim());
      }}
    >
      <Input
        autoFocus
        value={draft}
        disabled={busy}
        aria-label={`Rename ${title}`}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
          }
        }}
        className="h-7 text-sm"
      />
      <Button type="submit" size="xs" disabled={busy || !draft.trim()}>
        Save
      </Button>
    </form>
  );
}

export function BookSelector({
  books,
  selected,
  onChange,
  onRename,
  hasConversation,
  noun = "book",
}: BookSelectorProps) {
  const [open, setOpen] = useState(false);
  /** Which row is being renamed, and whether its save is in flight. */
  const [renaming, setRenaming] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const selectedSet = new Set(selected);
  const allSelected = books.length > 0 && selected.length === books.length;

  function toggle(bookId: number) {
    const next = new Set(selectedSet);
    if (next.has(bookId)) next.delete(bookId);
    else next.add(bookId);
    onChange([...next].sort((a, b) => a - b));
  }

  return (
    <div className="space-y-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="lg"
            className="w-full justify-between font-normal"
            aria-label={`Choose which ${noun}s to search`}
          >
            <span className="flex min-w-0 items-center gap-2">
              <Library className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="truncate">
                {describeSelection(books, selected, noun)}
              </span>
            </span>
            <ChevronsUpDown className="size-3.5 shrink-0 opacity-50" aria-hidden />
          </Button>
        </PopoverTrigger>

        <PopoverContent align="start" className="w-72 p-0">
          <div className="flex items-center justify-between px-3 py-2">
            <span className="text-xs font-medium text-muted-foreground">
              Search in
            </span>
            <Button
              variant="ghost"
              size="xs"
              onClick={() =>
                onChange(
                  allSelected ? [] : books.map((book) => book.book_id),
                )
              }
            >
              {allSelected ? "Deselect all" : "Select all"}
            </Button>
          </div>
          <Separator />

          <ul className="max-h-72 overflow-y-auto p-1">
            {books.map((book) => {
              const isSelected = selectedSet.has(book.book_id);
              const inputId = `book-${book.book_id}`;
              if (renaming === book.book_id && onRename) {
                return (
                  <li key={book.book_id}>
                    <RenameRow
                      title={book.title}
                      busy={saving}
                      onCancel={() => setRenaming(null)}
                      onSave={async (title) => {
                        setSaving(true);
                        try {
                          await onRename(book.book_id, title);
                          setRenaming(null);
                        } finally {
                          setSaving(false);
                        }
                      }}
                    />
                  </li>
                );
              }
              return (
                <li key={book.book_id} className="group/book relative">
                  <label
                    htmlFor={inputId}
                    className={cn(
                      "flex cursor-pointer items-start gap-3 rounded-md px-2 py-2 text-sm transition-colors hover:bg-accent",
                    )}
                  >
                    <Checkbox
                      id={inputId}
                      checked={isSelected}
                      onCheckedChange={() => toggle(book.book_id)}
                      className="mt-1"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">
                        {book.title}
                      </span>
                      {book.author && (
                        <span className="block truncate text-xs text-muted-foreground">
                          {book.author}
                        </span>
                      )}
                      {!book.retrieval_complete && (
                        <span className="block text-xs text-muted-foreground">
                          Search data still building
                        </span>
                      )}
                    </span>
                    {isSelected && (
                      <Check
                        className="mt-1 size-3.5 shrink-0 text-primary"
                        aria-hidden
                      />
                    )}
                  </label>
                  {/*
                    The way into source-first study, next to the way into
                    ask-first: a book is something to read as well as something
                    to ask about, and the library is where that choice belongs.
                    Outside the label for the same reason the rename button is
                    — a link inside one would toggle the checkbox it is for.
                  */}
                  <Button
                    asChild
                    size="icon-xs"
                    variant="ghost"
                    className={cn(
                      "absolute top-1 opacity-0 focus-visible:opacity-100 group-hover/book:opacity-100",
                      onRename ? "right-12" : "right-6",
                    )}
                  >
                    <Link
                      href={`/read/${book.book_id}`}
                      aria-label={`Read ${book.title}`}
                    >
                      <BookOpen aria-hidden />
                    </Link>
                  </Button>
                  {onRename ? (
                    /*
                      Outside the label on purpose: a button inside one toggles
                      the checkbox that label is for, so renaming would
                      deselect the document being renamed.
                    */
                    <Button
                      type="button"
                      size="icon-xs"
                      variant="ghost"
                      aria-label={`Rename ${book.title}`}
                      // Left of the selection tick rather than over it: hovering a row
                      // to rename it should not hide whether it is selected.
                      className="absolute right-6 top-1 opacity-0 focus-visible:opacity-100 group-hover/book:opacity-100"
                      onClick={() => setRenaming(book.book_id)}
                    >
                      <Pencil aria-hidden />
                    </Button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </PopoverContent>
      </Popover>

      {hasConversation && (
        <p className="text-xs text-muted-foreground">
          Changing this starts a new conversation — earlier answers were found
          in the previous selection.
        </p>
      )}
    </div>
  );
}
