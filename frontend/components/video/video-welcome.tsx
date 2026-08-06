"use client";

import { Button } from "@/components/ui/button";
import type { VideoChapter } from "@/lib/video-types";

const GENERIC_STARTERS = [
  "Summarize this lecture",
  "List the topics covered in this lecture",
  "What was drawn or written on the board?",
];

/**
 * Openers for a lecture with no conversation yet.
 *
 * The two whole-lecture requests come first because they are what a reader
 * opening an unfamiliar recording actually wants, and the graph now answers
 * them from the complete transcript rather than from a top-k sample.
 *
 * A chapter title makes a better third starter than anything generic: it
 * names something this recording demonstrably contains, in the lecturer's own
 * words, so the question retrieves rather than guesses.
 */
export function videoStarters(chapters: VideoChapter[]): string[] {
  // From the middle rather than the front. A recording opens on a title card,
  // branding and logistics — "Explain Stanford ENGINEERING" is a starter that
  // teaches a reader the feature does not work — and closes on a wrap-up. The
  // middle is where the lecture is about what it is about.
  const middle = chapters[Math.floor(chapters.length / 2)];
  const fromChapters = middle ? [`Explain ${middle.title}`] : [];
  return [
    ...GENERIC_STARTERS.slice(0, 2),
    ...fromChapters,
    GENERIC_STARTERS[2]!,
  ].slice(0, 3);
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
