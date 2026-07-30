"use client";

import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";

const STARTERS = [
  "What sections are present in Chapter 1?",
  "Summarize Chapter 1",
  "What causes training-serving skew?",
];

export function Welcome({
  hasBooks,
  canUseStarters = true,
  onAsk,
}: {
  hasBooks: boolean;
  canUseStarters?: boolean;
  onAsk: (question: string) => void;
}) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center py-12 text-center">
      <BrandMark className="mb-5" />
      <h2 className="font-heading text-2xl font-medium tracking-tight sm:text-3xl">
        {hasBooks ? "What would you like to understand?" : "Upload a book to begin"}
      </h2>
      <p className="mt-2 max-w-md text-sm text-muted-foreground">
        {hasBooks
          ? canUseStarters
            ? "Start with one of these, or ask your own question."
            : "Type @ in the question box to choose a book."
          : "Add a PDF from the library panel. It becomes selectable once processing and verification finish."}
      </p>

      {hasBooks && (
        <div className="mt-6 grid w-full max-w-md gap-2">
          {STARTERS.map((starter) => (
            <Button
              key={starter}
              variant="outline"
              size="lg"
              className="h-auto justify-start whitespace-normal px-4 py-3 text-left font-normal"
              onClick={() => onAsk(starter)}
              disabled={!canUseStarters}
            >
              {starter}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
