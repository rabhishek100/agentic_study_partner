"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useFittedCount } from "@/hooks/use-fitted-count";
import { Skeleton } from "@/components/ui/skeleton";
import { apiFetch } from "@/lib/api";
import type { SuggestedQuestionsResponse } from "@/lib/types";
import type { VideoChapter } from "@/lib/video-types";

const GENERIC_STARTERS = [
  "What is this lecture mainly about?",
  "Which topics should I focus on?",
  "What was the most important diagram?",
  "How could I use this in practice?",
  "What should I review next?",
];

function starterTopic(title: string): string {
  const afterPrefix = title.includes(":") ? title.split(":", 2)[1] : title;
  const words = (afterPrefix ?? title).trim().split(/\s+/).filter(Boolean);
  return words.slice(0, 5).join(" ") || "this topic";
}

export function videoStarters(chapters: VideoChapter[]): string[] {
  const middle = chapters[Math.floor(chapters.length / 2)];
  const fromChapters = middle
    ? [`Why does ${starterTopic(middle.title)} matter?`]
    : [];
  const starters = [
    ...GENERIC_STARTERS.slice(0, 2),
    ...fromChapters,
    ...GENERIC_STARTERS.slice(2),
  ];
  return Array.from(new Set(starters)).slice(0, 5);
}

export function VideoWelcome({
  videoId,
  chapters,
  canAsk,
  onAsk,
}: {
  videoId?: string;
  chapters: VideoChapter[];
  canAsk: boolean;
  onAsk(question: string): void;
}) {
  const [questions, setQuestions] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const fetchQuestions = useCallback(
    async (isRefresh = false) => {
      if (!videoId) return;
      if (isRefresh) {
        setRefreshing(true);
      } else {
        setLoading(true);
      }

      try {
        const endpoint = isRefresh
          ? `/videos/${videoId}/suggested-questions/refresh`
          : `/videos/${videoId}/suggested-questions`;
        const method = isRefresh ? "POST" : "GET";
        const data = await apiFetch<SuggestedQuestionsResponse>(endpoint, { method });
        if (data?.questions && data.questions.length > 0) {
          setQuestions(data.questions);
        }
      } catch {
        if (questions.length === 0) {
          setQuestions(videoStarters(chapters));
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [videoId, chapters, questions.length]
  );

  useEffect(() => {
    fetchQuestions(false);
  }, [fetchQuestions]);

  const activeQuestions =
    questions.length > 0 ? questions : videoStarters(chapters);

  // The empty state does not scroll, so the starter list gives up its tail
  // rather than pushing the pane past its bottom edge.
  const { frameRef, contentRef, itemsRef, count } = useFittedCount(
    activeQuestions.length
  );

  return (
    <div
      ref={frameRef}
      className="flex h-full min-h-0 flex-col overflow-hidden py-6 text-center [justify-content:safe_center]"
    >
      <div ref={contentRef} className="flex flex-col items-center">
        <h2 className="font-serif text-xl font-medium tracking-tight sm:text-2xl">
          What would you like to understand?
        </h2>
        <p className="mt-2 max-w-md text-sm text-muted-foreground">
          Ask about anything in this lecture — what was said, what was drawn, or
          what a slide shows. Answers cite the moment they came from.
        </p>

        {/* The items element stays mounted even when nothing fits: an unmounted
            list has no size to measure a way back from once the pane grows. */}
        <div
          className={`flex w-full max-w-md flex-col gap-2 ${count > 0 ? "mt-6" : ""}`}
        >
          {videoId && count > 0 && (
            <div className="flex items-center justify-between px-1 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <Sparkles className="h-3.5 w-3.5 text-primary" /> Suggested Prompts
              </span>
              <button
                type="button"
                onClick={() => fetchQuestions(true)}
                disabled={refreshing || loading}
                // 24px target floor; `-my-1` keeps the row its original height.
                className="-my-1 flex min-h-6 items-center gap-1 rounded px-2 py-1 text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
                title="Refresh suggested questions"
              >
                <RefreshCw className={`h-3 w-3 ${refreshing ? "animate-spin" : ""}`} />
                <span>Refresh</span>
              </button>
            </div>
          )}

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
                    disabled={!canAsk || refreshing}
                  >
                    {starter}
                  </Button>
                ))}
          </div>
        </div>
      </div>
    </div>
  );
}
