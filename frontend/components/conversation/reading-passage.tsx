"use client";

import { AlertCircle, BookOpen, ChevronDown } from "lucide-react";
import { useMemo } from "react";

import { InlineFigure } from "@/components/conversation/figures";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePassage } from "@/hooks/use-passage";
import type { PassageSegment, ReadingRef } from "@/lib/types";

/** The page number a reader sees, preferring the one printed on the page. */
function readerPage(segment: PassageSegment): number {
  return segment.printed_page ?? segment.page;
}

export function passageRange(reading: ReadingRef): string {
  const start = reading.printed_start_page ?? reading.start_page;
  const end = reading.printed_end_page ?? reading.end_page;
  return start === end ? `p. ${start}` : `pp. ${start}–${end}`;
}

/**
 * How far through the passage the loaded segments reach.
 *
 * Stated in pages and as a share, because "installment 3 of 9" is an
 * implementation detail — a reader tracks where they are in the chapter.
 */
function progress(
  segments: PassageSegment[],
  reading: ReadingRef,
): { percent: number; page: number | null } {
  if (segments.length === 0) return { percent: 0, page: null };
  const loaded = segments.reduce(
    (total, segment) => total + (segment.text?.length ?? 0),
    0,
  );
  const last = segments[segments.length - 1];
  return {
    percent:
      reading.total_characters > 0
        ? Math.min(100, Math.round((loaded / reading.total_characters) * 100))
        : 100,
    page: last ? readerPage(last) : null,
  };
}

function Heading({ segment }: { segment: PassageSegment }) {
  // Depth is the book's, not the document's: the scope root sits at whatever
  // level it occupies in the table of contents, so headings are rendered
  // relative to each other rather than mapped onto h1–h4 absolutely.
  const deep = (segment.level ?? 1) > 1;
  return (
    <h4
      className={
        deep
          ? "pt-2 font-sans text-sm font-semibold tracking-tight text-foreground"
          : "pt-3 font-sans text-base font-semibold tracking-tight text-foreground"
      }
    >
      {segment.text}
    </h4>
  );
}

function Table({ segment }: { segment: PassageSegment }) {
  return (
    <div className="reading-table -mx-1 overflow-x-auto rounded-md border border-border px-1 py-1">
      {segment.html ? (
        // Parser output from our own ingestion, not reader input, and it is
        // stripped to table markup at parse time. Rendering the flat text
        // instead would lose the row and column structure that is the only
        // reason a table is worth showing separately.
        <div dangerouslySetInnerHTML={{ __html: segment.html }} />
      ) : (
        <pre className="whitespace-pre-wrap font-mono text-xs">
          {segment.text}
        </pre>
      )}
    </div>
  );
}

/** A quiet rule carrying the page number, wherever the page turns. */
function PageTick({ page }: { page: number }) {
  return (
    <div className="flex items-center gap-2 pt-2" aria-hidden>
      <span className="h-px flex-1 bg-border" />
      {/* The eyebrow role: a micro-label that never carries sole meaning. */}
      <span className="font-sans text-eyebrow uppercase text-muted-foreground">
        p. {page}
      </span>
    </div>
  );
}

function Body({ segment }: { segment: PassageSegment }) {
  switch (segment.kind) {
    case "heading":
      return <Heading segment={segment} />;
    case "table":
      return <Table segment={segment} />;
    case "figure":
      return segment.figure ? <InlineFigure figure={segment.figure} /> : null;
    case "caption":
      return (
        <p className="font-sans text-[0.8em] text-muted-foreground">
          {segment.text}
        </p>
      );
    case "formula":
      // Not typeset as maths: the parser stores the formula as text, and
      // rendering it through KaTeX would be guessing at markup it never had.
      // A monospaced, scrollable line is honest about that.
      return (
        <pre className="overflow-x-auto rounded bg-muted px-3 py-2 font-mono text-[0.85em]">
          {segment.text}
        </pre>
      );
    default:
      return <p>{segment.text}</p>;
  }
}

/**
 * Consecutive list items, as one list.
 *
 * Grouped rather than rendered as individual bulleted rows because a screen
 * reader announces "list, 4 items" only for a real list, and a definitions
 * block a reader is working through is exactly where that count helps.
 */
type Run = [PassageSegment, ...PassageSegment[]];

function grouped(segments: PassageSegment[]): Run[] {
  const runs: Run[] = [];
  for (const segment of segments) {
    const last = runs[runs.length - 1];
    const continues =
      segment.kind === "list_item" &&
      last !== undefined &&
      last[0].kind === "list_item" &&
      // A list broken by a page turn is two lists, so the page tick lands
      // between them rather than inside a single <ul>.
      readerPage(last[last.length - 1]!) === readerPage(segment);
    if (continues) last!.push(segment);
    else runs.push([segment]);
  }
  return runs;
}

function RunView({
  run,
  previous,
}: {
  run: Run;
  previous: PassageSegment | undefined;
}) {
  const first = run[0];
  const turned =
    previous !== undefined && readerPage(first) !== readerPage(previous);

  return (
    <>
      {turned ? <PageTick page={readerPage(first)} /> : null}
      {first.kind === "list_item" ? (
        <ul className="list-disc pl-[1.2em]">
          {run.map((segment) => (
            <li key={segment.index}>{segment.text}</li>
          ))}
        </ul>
      ) : (
        <Body segment={first} />
      )}
    </>
  );
}

export interface ReadingPassageProps {
  reading: ReadingRef;
}

/**
 * A chapter or paper reproduced into the conversation, not summarized.
 *
 * The only surface in the chat typeset for sustained reading rather than for
 * scanning an answer. It is deliberately framed as source material — its own
 * card, headed with the book and the page range — because everything else in
 * this column is generated text, and a reader has to be able to tell at a
 * glance which is which.
 */
export function ReadingPassage({ reading }: ReadingPassageProps) {
  const { segments, status, error, hasMore, loadMore, retry } =
    usePassage(reading);
  const reached = useMemo(
    () => progress(segments, reading),
    [segments, reading],
  );
  const runs = useMemo(() => grouped(segments), [segments]);

  return (
    <section
      className="rounded-lg border border-border bg-card"
      aria-label={`${reading.display_path}, read in full from ${reading.book_title}`}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-3 py-2 font-sans text-xs text-muted-foreground sm:px-4">
        <BookOpen className="size-3.5 shrink-0" aria-hidden />
        <span className="font-medium text-foreground">
          {reading.display_path}
        </span>
        <span aria-hidden>·</span>
        <span>{passageRange(reading)}</span>
        <span aria-hidden>·</span>
        <span>{reading.book_title}</span>
      </header>

      <div className="px-3 py-3 sm:px-5 sm:py-4">
        {status === "loading" && segments.length === 0 && (
          <div className="space-y-2" aria-hidden>
            {[0, 1, 2, 3, 4].map((row) => (
              <Skeleton
                key={row}
                className={row % 4 === 3 ? "h-4 w-2/3" : "h-4 w-full"}
              />
            ))}
          </div>
        )}

        {segments.length > 0 && (
          <div className="reading-passage">
            {runs.map((run, index) => (
              <RunView
                key={run[0].index}
                run={run}
                previous={runs[index - 1]?.at(-1)}
              />
            ))}
          </div>
        )}

        {error && (
          <Alert variant="destructive" className="mt-3">
            <AlertCircle aria-hidden />
            <AlertDescription className="flex flex-wrap items-center gap-2">
              {error}
              <Button variant="outline" size="xs" onClick={retry}>
                Try again
              </Button>
            </AlertDescription>
          </Alert>
        )}

        {/*
          Announced rather than the text itself: appending a chapter into a
          live region would read the whole installment aloud on arrival.
        */}
        <p className="sr-only" role="status" aria-live="polite">
          {status === "extending"
            ? "Loading more of the passage."
            : reached.page !== null
            ? `Loaded through page ${reached.page}, about ${reached.percent} percent of the passage.`
            : ""}
        </p>

        {segments.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-3 font-sans text-xs text-muted-foreground">
            {hasMore ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={loadMore}
                  disabled={status === "extending"}
                >
                  <ChevronDown aria-hidden />
                  {status === "extending" ? "Loading…" : "Continue reading"}
                </Button>
                <span>
                  Through p. {reached.page} · {reached.percent}% of{" "}
                  {reading.kind === "book" ? "the document" : `this ${reading.kind}`}
                </span>
              </>
            ) : (
              <span>
                End of {reading.kind === "book" ? "the document" : reading.kind}
                {reading.omitted_block_count > 0 && (
                  <>
                    {" "}
                    · running heads, footers and decorative images omitted
                  </>
                )}
              </span>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
