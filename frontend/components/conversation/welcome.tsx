"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useFittedCount } from "@/hooks/use-fitted-count";
import { apiFetch } from "@/lib/api";
import type { SuggestedQuestionsResponse } from "@/lib/types";

const FALLBACK_STARTERS = [
  "What is the main idea?",
  "Why does this topic matter?",
  "How could I use this in practice?",
  "What is a common mistake here?",
  "Which idea should I review next?",
];

export function Welcome({
  hasBooks,
  selectedBookIds = [],
  canUseStarters = true,
  onAsk,
}: {
  hasBooks: boolean;
  selectedBookIds?: number[];
  canUseStarters?: boolean;
  onAsk: (question: string) => void;
}) {
  const [questions, setQuestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const bookIdsParam = selectedBookIds.length > 0 ? selectedBookIds.join(",") : "";

  const fetchQuestions = useCallback(async (isRefresh = false) => {
    if (!hasBooks) return;
    if (isRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }

    try {
      const endpoint = isRefresh
        ? `/books/suggested-questions/refresh${bookIdsParam ? `?book_ids=${bookIdsParam}` : ""}`
        : `/books/suggested-questions${bookIdsParam ? `?book_ids=${bookIdsParam}` : ""}`;
      
      const method = isRefresh ? "POST" : "GET";
      const data = await apiFetch<SuggestedQuestionsResponse>(endpoint, { method });
      if (data?.questions && data.questions.length > 0) {
        setQuestions(data.questions);
      }
    } catch {
      if (questions.length === 0) {
        setQuestions(FALLBACK_STARTERS);
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [hasBooks, bookIdsParam, questions.length]);

  useEffect(() => {
    fetchQuestions(false);
  }, [fetchQuestions]);

  const activeQuestions = questions.length > 0 ? questions : FALLBACK_STARTERS;

  // The empty state does not scroll, so the starter list gives up its tail
  // rather than pushing the page past the bottom of the pane.
  const { frameRef, contentRef, itemsRef, count } = useFittedCount(
    activeQuestions.length
  );

  return (
    <div
      ref={frameRef}
      className="flex h-full min-h-0 flex-col overflow-hidden py-6 text-center [justify-content:safe_center]"
    >
      <div ref={contentRef} className="flex flex-col items-center">
        <BrandMark className="mb-6" />
        <h2 className="font-serif text-2xl font-medium tracking-tight sm:text-3xl">
          {hasBooks ? "What would you like to understand?" : "Upload a book to begin"}
        </h2>
        <p className="mt-2 max-w-md text-sm text-muted-foreground">
          {hasBooks
            ? canUseStarters
              ? "Start with one of these dynamic prompts, or ask your own question."
              : "Type @ in the question box to choose a book."
            : "Add a PDF from the library panel. It becomes selectable once processing and verification finish."}
        </p>

        {hasBooks && (
          // The items element stays mounted even when nothing fits: an unmounted
          // list has no size to measure a way back from once the pane grows.
          <div
            className={`flex w-full max-w-md flex-col gap-2 ${count > 0 ? "mt-6" : ""}`}
          >
            <div
              className={`items-center justify-between px-1 text-xs text-muted-foreground ${
                count > 0 ? "flex" : "hidden"
              }`}
            >
              <span className="flex items-center gap-1">
                <Sparkles className="h-3.5 w-3.5 text-primary" /> Suggested Prompts
              </span>
              <button
                type="button"
                onClick={() => fetchQuestions(true)}
                disabled={refreshing || loading}
                className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
                title="Refresh suggested questions"
              >
                <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
                <span>Refresh</span>
              </button>
            </div>

            <div ref={itemsRef} className="flex flex-col gap-2">
              {loading
                ? Array.from({ length: Math.min(5, count) }).map((_, i) => (
                    <Skeleton key={i} className="h-12 w-full rounded-md" />
                  ))
                : activeQuestions.slice(0, count).map((starter) => (
                    <Button
                      key={starter}
                      variant="outline"
                      size="lg"
                      className="h-auto justify-start whitespace-normal px-4 py-3 text-left font-normal"
                      onClick={() => onAsk(starter)}
                      disabled={!canUseStarters || refreshing}
                    >
                      {starter}
                    </Button>
                  ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
