"use client";

import { Button } from "@/components/ui/button";
import type { VideoChapter } from "@/lib/video-types";

const GENERIC_STARTERS = [
  "What was drawn or written on the board?",
  "What do the slides cover?",
  "Explain the main idea of this lecture.",
];

/**
 * Openers for a lecture with no conversation yet.
 *
 * A chapter title is a far better starter than anything generic, because it
 * names something this recording demonstrably contains — the lecturer's own
 * words for it, so the question retrieves rather than guesses. Chapters are
 * used when the source published them and the generic set is the fallback.
 *
 * Everything here is answerable by retrieval today. Whole-lecture requests
 * ("summarize this video") are deliberately absent until the graph can serve
 * them from the full transcript rather than from a top-k sample.
 */
export function videoStarters(chapters: VideoChapter[]): string[] {
  const fromChapters = chapters
    .slice(0, 2)
    .map((chapter) => `Explain ${chapter.title}`);
  return [...fromChapters, ...GENERIC_STARTERS].slice(0, 3);
}

export function VideoWelcome({
  chapters,
  canAsk,
  onAsk,
}: {
  chapters: VideoChapter[];
  canAsk: boolean;
  onAsk(question: string): void;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-10 text-center">
      <h2 className="font-heading text-xl font-medium tracking-tight sm:text-2xl">
        What would you like to understand?
      </h2>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        Ask about anything in this lecture — what was said, what was drawn, or
        what a slide shows. Answers cite the moment they came from.
      </p>

      <div className="mt-6 grid w-full max-w-md gap-2">
        {videoStarters(chapters).map((starter) => (
          <Button
            key={starter}
            variant="outline"
            size="lg"
            className="h-auto justify-start whitespace-normal px-4 py-3 text-left font-normal"
            onClick={() => onAsk(starter)}
            disabled={!canAsk}
          >
            {starter}
          </Button>
        ))}
      </div>
    </div>
  );
}
