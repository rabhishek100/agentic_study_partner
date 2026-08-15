"use client";

import { Check, ChevronsUpDown, Library } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import type { BookSummary } from "@/lib/types";
import { cn } from "@/lib/utils";

/** How the current selection reads in one line. */
export function describeSelection(
  books: BookSummary[],
  selected: number[],
  documentType: "book" | "paper" = "book",
): string {
  const singular = documentType;
  const plural = documentType === "paper" ? "papers" : "books";
  if (books.length === 0) return `No ${plural}`;
  if (selected.length === 0) return `No ${plural} selected`;
  if (selected.length === books.length) {
    return books.length === 1
      ? (books[0]?.title ?? `1 ${singular}`)
      : `All ${books.length} ${plural}`;
  }
  if (selected.length === 1) {
    const only = books.find((book) => book.book_id === selected[0]);
    return only?.title ?? `1 ${singular}`;
  }
  return `${selected.length} of ${books.length} ${plural}`;
}

export interface BookSelectorProps {
  books: BookSummary[];
  selected: number[];
  onChange: (bookIds: number[]) => void;
  /** True once the conversation has turns that were answered under `selected`. */
  hasConversation: boolean;
  documentType?: "book" | "paper";
}

export function BookSelector({
  books,
  selected,
  onChange,
  hasConversation,
  documentType = "book",
}: BookSelectorProps) {
  const [open, setOpen] = useState(false);
  const selectedSet = new Set(selected);
  const allSelected = books.length > 0 && selected.length === books.length;

  function toggle(bookId: number) {
    const next = new Set(selectedSet);
    if (next.has(bookId)) next.delete(bookId);
    else next.add(bookId);
    onChange([...next].sort((a, b) => a - b));
  }

  return (
    <div className="space-y-1.5">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            size="lg"
            className="w-full justify-between font-normal"
            aria-label={`Choose which ${documentType === "paper" ? "papers" : "books"} to search`}
          >
            <span className="flex min-w-0 items-center gap-2">
              <Library className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              <span className="truncate">
                {describeSelection(books, selected, documentType)}
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
              return (
                <li key={book.book_id}>
                  <label
                    htmlFor={inputId}
                    className={cn(
                      "flex cursor-pointer items-start gap-2.5 rounded-md px-2 py-2 text-sm transition-colors hover:bg-accent",
                    )}
                  >
                    <Checkbox
                      id={inputId}
                      checked={isSelected}
                      onCheckedChange={() => toggle(book.book_id)}
                      className="mt-0.5"
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
                        className="mt-0.5 size-3.5 shrink-0 text-primary"
                        aria-hidden
                      />
                    )}
                  </label>
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
