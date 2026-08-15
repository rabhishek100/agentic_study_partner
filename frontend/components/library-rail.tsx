"use client";

import {
  AlertCircle,
  ChevronDown,
  Library,
  RotateCcw,
  Settings2,
} from "lucide-react";
import { useState } from "react";

import { BookSelector } from "@/components/book-selector";
import {
  ConversationHistory,
  type ConversationHistoryProps,
} from "@/components/conversation-history";
import { UploadPanel } from "@/components/upload-panel";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
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
import type { BookSummary, RetrievalMode } from "@/lib/types";
import { cn } from "@/lib/utils";

const RETRIEVAL_MODES: {
  value: RetrievalMode;
  label: string;
  hint: string;
}[] = [
  {
    value: "hybrid_rerank",
    label: "Hybrid + reranking",
    hint: "Best measured accuracy — the default",
  },
  { value: "hybrid", label: "Hybrid", hint: "Keyword and meaning combined" },
  { value: "bm25", label: "Keyword", hint: "Exact terms only" },
  { value: "vector", label: "Meaning-based", hint: "Paraphrase tolerant" },
];

function SectionHeading({
  children,
  id,
}: {
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <h2
      id={id}
      className="text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground"
    >
      {children}
    </h2>
  );
}

export interface LibraryRailProps {
  books: BookSummary[];
  booksLoaded: boolean;
  booksError: string;
  onRetryLoadBooks: () => void;
  selectedBookIds: number[];
  onSelectBooks: (bookIds: number[]) => void;
  retrievalMode: RetrievalMode;
  onRetrievalModeChange: (mode: RetrievalMode) => void;
  hasConversation: boolean;
  onBooksChanged: () => void;
  history: ConversationHistoryProps;
  documentType?: "book" | "paper";
}

export function LibraryRail({
  books,
  booksLoaded,
  booksError,
  onRetryLoadBooks,
  selectedBookIds,
  onSelectBooks,
  retrievalMode,
  onRetrievalModeChange,
  hasConversation,
  onBooksChanged,
  history,
  documentType = "book",
}: LibraryRailProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const hasBooks = books.length > 0;

  return (
    <div className="flex h-full flex-col gap-5 overflow-y-auto overscroll-contain p-4 [scrollbar-gutter:stable]">
      <section aria-labelledby="library-heading" className="space-y-2">
        <SectionHeading id="library-heading">
          {documentType === "paper" ? "Your paper library" : "Your library"}
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
          <BookSelector
            books={books}
            selected={selectedBookIds}
            onChange={onSelectBooks}
            hasConversation={hasConversation}
            documentType={documentType}
          />
        ) : (
          <div className="rounded-lg border border-dashed border-input px-3 py-6 text-center">
            <Library
              className="mx-auto mb-2 size-5 text-muted-foreground"
              aria-hidden
            />
            <p className="text-xs text-muted-foreground">
              {documentType === "paper"
                ? "No papers yet. Upload a PDF paper to get started."
                : "No books yet. Upload a PDF to get started."}
            </p>
          </div>
        )}
      </section>

      <Separator />

      <ConversationHistory {...history} />

      <Separator />

      <UploadPanel onBookReady={onBooksChanged} documentType={documentType} />

      <Separator />

      {/*
        Retrieval mode is an advanced control, not a front-door choice: a
        reader has no basis for preferring "keyword" over "meaning-based"
        before seeing an answer. Which mode ran is reported per answer in the
        inspector, where it is actually interpretable.
      */}
      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <CollapsibleTrigger className="flex w-full items-center gap-1.5 text-[0.7rem] font-semibold uppercase tracking-[0.1em] text-muted-foreground transition-colors hover:text-foreground">
          <Settings2 className="size-3.5" aria-hidden />
          Advanced
          <ChevronDown
            className={cn(
              "ml-auto size-3.5 transition-transform",
              advancedOpen && "rotate-180",
            )}
            aria-hidden
          />
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-2 space-y-1.5">
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
            <p className="text-xs text-muted-foreground">
              {
                RETRIEVAL_MODES.find((mode) => mode.value === retrievalMode)
                  ?.hint
              }
            </p>
          </div>
        </CollapsibleContent>
      </Collapsible>

    </div>
  );
}
