"use client";

import {
  AlertCircle,
  BookOpen,
  ChevronDown,
  FileText,
  Loader2,
  Pause,
  Play,
  Volume2,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { InlineFigure } from "@/components/conversation/figures";
import { CaptionText } from "@/components/conversation/caption-text";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { usePassage } from "@/hooks/use-passage";
import { useReadAloud } from "@/hooks/use-read-aloud";
import { buildPassageNarrationScript, presentablePassageSegments } from "@/lib/narration";
import { fetchCompletePassage } from "@/lib/passage";
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

function Table({ segment }: { segment: PassageSegment }) {
  return (
    <div className="reading-table overflow-x-auto rounded-lg border border-border bg-raised">
      {segment.html ? (
        // Parser output from our own ingestion, not reader input, and it is
        // stripped to table markup at parse time. Rendering the flat text
        // instead would lose the row and column structure that is the only
        // reason a table is worth showing separately.
        <div dangerouslySetInnerHTML={{ __html: segment.html }} />
      ) : (
        <pre className="whitespace-pre-wrap p-4 font-mono text-xs leading-relaxed">
          {segment.text}
        </pre>
      )}
    </div>
  );
}

/** A quiet rule carrying the page number, wherever the page turns. */
function PageTick({ page }: { page: number }) {
  return (
    <div className="reading-page-tick" aria-hidden>
      <span className="h-px flex-1 bg-divider" />
      {/* The eyebrow role: a micro-label that never carries sole meaning. */}
      <span className="rounded-full border border-divider bg-canvas px-3 py-1 font-sans text-eyebrow uppercase tracking-[0.12em] text-muted-foreground">
        p. {page}
      </span>
      <span className="h-px flex-1 bg-divider" />
    </div>
  );
}

function Body({
  segment,
  baseHeadingLevel,
  sourceCaption,
  captionIndex,
}: {
  segment: PassageSegment;
  baseHeadingLevel: number;
  sourceCaption?: string | null;
  captionIndex?: number;
}) {
  const narration = { "data-narration-passage": segment.index };
  switch (segment.kind) {
    case "heading":
      if (Math.max(0, (segment.level ?? baseHeadingLevel) - baseHeadingLevel) === 0) {
        return <h3 {...narration} className="reading-heading reading-heading-primary">{segment.text}</h3>;
      }
      if (Math.max(0, (segment.level ?? baseHeadingLevel) - baseHeadingLevel) === 1) {
        return <h4 {...narration} className="reading-heading reading-heading-secondary">{segment.text}</h4>;
      }
      return <h5 {...narration} className="reading-heading reading-heading-tertiary">{segment.text}</h5>;
    case "table":
      return <div {...narration}><Table segment={segment} /></div>;
    case "figure":
      return segment.figure ? (
        <InlineFigure
          figure={segment.figure}
          visibleCaption={sourceCaption ?? null}
          narrationPassageIndex={segment.index}
          captionNarrationPassageIndex={captionIndex}
        />
      ) : null;
    case "caption":
      return (
        <p {...narration} className="reading-caption">
          {segment.text}
        </p>
      );
    case "formula":
      // Not typeset as maths: the parser stores the formula as text, and
      // rendering it through KaTeX would be guessing at markup it never had.
      // A monospaced, scrollable line is honest about that.
      return (
        <div {...narration} className="reading-formula" role="group" aria-label="Formula">
          <span className="reading-formula-label" aria-hidden>
            Formula
          </span>
          <pre>{segment.text}</pre>
        </div>
      );
    default:
      return (
        <p {...narration}>
          {segment.text && !/<[a-z][\s\S]*>/i.test(segment.text) ? (
            <CaptionText>{segment.text}</CaptionText>
          ) : (
            segment.text
          )}
        </p>
      );
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
      last !== undefined &&
      readerPage(last[last.length - 1]!) === readerPage(segment) &&
      ((segment.kind === "list_item" && last[0].kind === "list_item") ||
        // A source caption belongs to the image immediately before it. Keeping
        // them in one figure prevents derived alt copy and the printed caption
        // from looking like two unrelated paragraphs.
        (segment.kind === "caption" &&
          last.length === 1 &&
          last[0].kind === "figure"));
    if (continues) last!.push(segment);
    else runs.push([segment]);
  }
  return runs;
}

function listItemText(text: string | null): string {
  // The outer semantic list supplies the marker. Some imported blocks retain
  // their Markdown marker too; removing only that prefix prevents a double
  // bullet while leaving the stored string and all meaningful text untouched.
  return (text ?? "")
    .replace(/^\s*[-*•]\s+/, "")
    .replace(/^\s*\d+[.)]\s+/, "");
}

function orderedListStart(run: Run): number | null {
  if (!run.every((segment) => /^\s*\d+[.)]\s+/.test(segment.text ?? ""))) {
    return null;
  }
  const match = /^\s*(\d+)/.exec(run[0].text ?? "");
  return match ? Number(match[1]) : 1;
}

function headingBase(segments: PassageSegment[]): number {
  let base = Number.POSITIVE_INFINITY;
  for (const segment of segments) {
    if (segment.kind === "heading") {
      base = Math.min(base, segment.level ?? 1);
    }
  }
  return Number.isFinite(base) ? base : 1;
}

function RunView({
  run,
  previous,
  baseHeadingLevel,
}: {
  run: Run;
  previous: PassageSegment | undefined;
  baseHeadingLevel: number;
}) {
  const first = run[0];
  const turned =
    previous !== undefined && readerPage(first) !== readerPage(previous);

  return (
    <>
      {turned ? <PageTick page={readerPage(first)} /> : null}
      {first.kind === "list_item" ? (
        (() => {
          const start = orderedListStart(run);
          const items = run.map((segment) => (
            <li key={segment.index} data-narration-passage={segment.index}>
              <CaptionText>{listItemText(segment.text)}</CaptionText>
            </li>
          ));
          return start === null ? (
            <ul className="reading-list reading-list-bulleted">{items}</ul>
          ) : (
            <ol className="reading-list reading-list-ordered" start={start}>
              {items}
            </ol>
          );
        })()
      ) : (
        <Body
          segment={first}
          baseHeadingLevel={baseHeadingLevel}
          sourceCaption={run[1]?.kind === "caption" ? run[1].text : undefined}
          captionIndex={run[1]?.kind === "caption" ? run[1].index : undefined}
        />
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
  const displayedSegments = useMemo(
    () => presentablePassageSegments(segments, reading.display_path),
    [segments, reading.display_path],
  );
  const runs = useMemo(() => grouped(displayedSegments), [displayedSegments]);
  const baseHeadingLevel = useMemo(
    () => headingBase(displayedSegments),
    [displayedSegments],
  );
  const narration = useReadAloud();
  const narrationId = `reading-${reading.book_id}-${reading.node_id ?? "whole"}`;
  const isActive = narration.activeId === narrationId;
  const [preparingChapter, setPreparingChapter] = useState(false);
  const [narrationError, setNarrationError] = useState("");
  const prepareRun = useRef(0);

  useEffect(() => () => {
    prepareRun.current += 1;
  }, []);

  useEffect(() => {
    const anchor = narration.currentAnchor;
    if (
      !isActive ||
      anchor?.type !== "passage" ||
      status === "loading" ||
      status === "extending" ||
      !hasMore
    ) return;
    const lastIndex = segments.at(-1)?.index ?? -1;
    if (anchor.index > lastIndex) loadMore();
  }, [hasMore, isActive, loadMore, narration.currentAnchor, segments, status]);

  const toggleChapterNarration = async () => {
    if (isActive) {
      if (narration.status === "paused") narration.resume();
      else narration.pause();
      return;
    }
    const run = ++prepareRun.current;
    setPreparingChapter(true);
    setNarrationError("");
    try {
      const complete = await fetchCompletePassage(reading);
      if (run !== prepareRun.current) return;
      void narration.playScript(
        narrationId,
        buildPassageNarrationScript({ reading, segments: complete }),
        { anchorId: narrationId, label: reading.display_path },
      );
    } catch (cause) {
      if (run !== prepareRun.current) return;
      setNarrationError(
        cause instanceof Error ? cause.message : "The chapter could not be prepared for reading.",
      );
    } finally {
      if (run === prepareRun.current) setPreparingChapter(false);
    }
  };

  return (
    <section
      className="reading-document overflow-hidden rounded-xl border border-border bg-card"
      aria-label={`${reading.display_path}, read in full from ${reading.book_title}`}
      data-narration-anchor={narrationId}
    >
      <header className="reading-document-header">
        <div className="flex items-center gap-2 font-sans text-eyebrow font-semibold uppercase tracking-[0.14em] text-evidence">
          <span className="grid size-7 place-items-center rounded-full bg-wash">
            <BookOpen className="size-3.5" aria-hidden />
          </span>
          Full {reading.kind}
        </div>
        <h2 className="mt-3 max-w-[34ch] font-heading text-xl font-medium tracking-tight text-foreground sm:text-2xl">
          {reading.display_path}
        </h2>
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 font-sans text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-2">
            <FileText className="size-3.5" aria-hidden />
            {passageRange(reading)}
          </span>
          <span
            className="hidden size-1 rounded-full bg-divider sm:block"
            aria-hidden
          />
          <span>{reading.book_title}</span>
        </div>
        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void toggleChapterNarration()}
            disabled={preparingChapter}
            aria-label={
              isActive && narration.status !== "paused"
                ? "Pause chapter"
                : isActive
                  ? "Resume chapter"
                  : "Read entire chapter aloud"
            }
          >
            {preparingChapter ? (
              <Loader2 className="animate-spin" aria-hidden />
            ) : isActive && narration.status !== "paused" ? (
              <Pause aria-hidden />
            ) : isActive ? (
              <Play aria-hidden />
            ) : (
              <Volume2 aria-hidden />
            )}
            {preparingChapter
              ? "Preparing full chapter…"
              : isActive && narration.status !== "paused"
                ? "Pause"
                : isActive
                  ? "Resume"
                  : "Read entire chapter aloud"}
          </Button>
          <span className="text-xs text-muted-foreground">
            Uses the pacing controls in the player.
          </span>
        </div>
        {narrationError ? (
          <p className="mt-2 text-xs text-destructive" role="alert">{narrationError}</p>
        ) : null}
      </header>

      <div className="px-4 py-6 sm:px-8 sm:py-9 lg:px-10">
        {status === "loading" && segments.length === 0 && (
          <div className="mx-auto max-w-[62ch] space-y-3" aria-hidden>
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
                baseHeadingLevel={baseHeadingLevel}
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
          <div className="reading-document-footer">
            {hasMore ? (
              <div className="w-full">
                <div
                  className="mb-3 h-1 overflow-hidden rounded-full bg-divider"
                  aria-hidden
                >
                  <span
                    className="block h-full rounded-full bg-action"
                    style={{ width: `${reached.percent}%` }}
                  />
                </div>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={loadMore}
                    disabled={status === "extending"}
                  >
                    <ChevronDown aria-hidden />
                    {status === "extending"
                      ? "Loading…"
                      : "Continue reading"}
                  </Button>
                  <span>
                    Through p. {reached.page} · {reached.percent}% of{" "}
                    {reading.kind === "book"
                      ? "the document"
                      : `this ${reading.kind}`}
                  </span>
                </div>
              </div>
            ) : (
              <span className="inline-flex items-center gap-2">
                <span
                  className="size-1.5 rounded-full bg-evidence"
                  aria-hidden
                />
                End of{" "}
                {reading.kind === "book" ? "the document" : reading.kind}
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
