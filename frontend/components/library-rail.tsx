"use client";

import { AlertCircle, Library, RotateCcw, Trash2 } from "lucide-react";

import { UploadPanel } from "@/components/upload-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import type { BookSummary, RetrievalMode, TurnResult } from "@/lib/types";

const RETRIEVAL_MODES: { value: RetrievalMode; label: string }[] = [
  { value: "hybrid", label: "Hybrid (recommended)" },
  { value: "bm25", label: "Keyword" },
  { value: "vector", label: "Meaning-based" },
  { value: "hybrid_rerank", label: "Hybrid + reranking" },
];

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
      {children}
    </h2>
  );
}

function Detail({ term, value }: { term: string; value: string }) {
  return (
    <div className="space-y-0.5 border-b border-border py-2 last:border-b-0">
      <dt className="text-[0.7rem] text-muted-foreground">{term}</dt>
      <dd className="break-words text-xs leading-snug">{value}</dd>
    </div>
  );
}

export interface LibraryRailProps {
  books: BookSummary[];
  booksLoaded: boolean;
  booksError: string;
  onRetryLoadBooks: () => void;
  selectedBookId: number | null;
  onSelectBook: (bookId: number) => void;
  retrievalMode: RetrievalMode;
  onRetrievalModeChange: (mode: RetrievalMode) => void;
  lastResult: TurnResult | null;
  activeScope: string;
  hasConversation: boolean;
  onClearConversation: () => void;
  onBooksChanged: () => void;
}

export function LibraryRail({
  books,
  booksLoaded,
  booksError,
  onRetryLoadBooks,
  selectedBookId,
  onSelectBook,
  retrievalMode,
  onRetrievalModeChange,
  lastResult,
  activeScope,
  hasConversation,
  onClearConversation,
  onBooksChanged,
}: LibraryRailProps) {
  const selectedBook = books.find((book) => book.book_id === selectedBookId);
  const hasBooks = books.length > 0;

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">
      <section aria-labelledby="library-heading" className="space-y-2">
        <SectionHeading>
          <span id="library-heading">Your library</span>
        </SectionHeading>

        {!booksLoaded ? (
          <div className="space-y-2" aria-hidden>
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : booksError ? (
          <Alert variant="destructive">
            <AlertCircle aria-hidden />
            <AlertTitle>Could not load your library</AlertTitle>
            <AlertDescription>
              {booksError}
              <Button
                variant="outline"
                size="sm"
                className="mt-2"
                onClick={onRetryLoadBooks}
              >
                <RotateCcw aria-hidden />
                Try again
              </Button>
            </AlertDescription>
          </Alert>
        ) : hasBooks ? (
          <div className="space-y-1.5">
            <Label htmlFor="book-select">Book</Label>
            <Select
              value={selectedBookId ? String(selectedBookId) : undefined}
              onValueChange={(value) => onSelectBook(Number(value))}
            >
              <SelectTrigger id="book-select" className="w-full">
                <SelectValue placeholder="Choose a book" />
              </SelectTrigger>
              <SelectContent>
                {books.map((book) => (
                  <SelectItem key={book.book_id} value={String(book.book_id)}>
                    {book.title}
                    {book.author ? ` — ${book.author}` : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {selectedBook && !selectedBook.retrieval_complete && (
              <p className="text-xs text-muted-foreground">
                Search data for this book is still building.
              </p>
            )}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed border-input px-3 py-6 text-center">
            <Library
              className="mx-auto mb-2 size-5 text-muted-foreground"
              aria-hidden
            />
            <p className="text-xs text-muted-foreground">
              No books yet. Upload a PDF to get started.
            </p>
          </div>
        )}

        <div className="space-y-1.5 pt-1">
          <Label htmlFor="retrieval-mode">Search method</Label>
          <Select
            value={retrievalMode}
            onValueChange={(value) =>
              onRetrievalModeChange(value as RetrievalMode)
            }
          >
            <SelectTrigger id="retrieval-mode" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RETRIEVAL_MODES.map((mode) => (
                <SelectItem key={mode.value} value={mode.value}>
                  {mode.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </section>

      <Separator />

      <UploadPanel onBookReady={onBooksChanged} />

      <Separator />

      <section aria-labelledby="last-turn-heading" className="space-y-1">
        <SectionHeading>
          <span id="last-turn-heading">Last turn</span>
        </SectionHeading>
        <dl>
          <Detail term="Route" value={lastResult?.route ?? "Not applicable"} />
          <Detail
            term="Search query"
            value={lastResult?.standalone_query ?? "Not applicable"}
          />
          <Detail term="Active scope" value={activeScope} />
          <Detail
            term="Outcome"
            value={lastResult?.outcome ?? "Not applicable"}
          />
        </dl>
      </section>

      <Button
        variant="outline"
        size="sm"
        className="mt-auto"
        onClick={onClearConversation}
        disabled={!hasConversation}
      >
        <Trash2 aria-hidden />
        Clear conversation
      </Button>
    </div>
  );
}
