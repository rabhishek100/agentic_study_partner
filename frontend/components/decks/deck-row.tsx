"use client";

import {
  AlertCircle,
  AlertTriangle,
  BookOpen,
  Check,
  Circle,
  FileText,
  LoaderCircle,
  Sparkles,
  Video,
  X,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  type DeckJob,
  type DeckMetrics,
  type DeckSummary,
  coveragePercent,
  deckJobError,
  formatDeckDuration,
  jobIsLive,
} from "@/lib/deck-types";
import { cn } from "@/lib/utils";

export function CoverageBadge({
  metrics,
  generationMode,
}: {
  metrics: DeckMetrics;
  generationMode?: DeckSummary["generation_mode"];
}) {
  const legacyQuestionDeck =
    generationMode === "book_extracted" &&
    metrics.source_items_total === 0 &&
    metrics.source_questions_total === 0 &&
    !metrics.notice;
  const percent = coveragePercent(metrics);
  const sourceItemCoverage = metrics.source_items_total > 0;
  const questionCoverage = metrics.source_questions_total > 0;
  const extractedCoverage = sourceItemCoverage || questionCoverage;
  const sourceCovered = sourceItemCoverage
    ? metrics.source_items_covered
    : metrics.source_questions_covered;
  const sourceTotal = sourceItemCoverage
    ? metrics.source_items_total
    : metrics.source_questions_total;
  const uncovered = sourceItemCoverage
    ? metrics.uncovered_source_items.map((item) => item.label)
    : questionCoverage
      ? metrics.uncovered_question_labels
      : metrics.uncovered_topic_labels;
  const complete =
    !legacyQuestionDeck &&
    (extractedCoverage
      ? sourceCovered >= sourceTotal
      : metrics.topics_covered >= metrics.topics_required);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant={complete ? "secondary" : "outline"}
          className="gap-1 font-normal tabular-nums"
        >
          {!complete ? <AlertTriangle aria-hidden className="size-3" /> : null}
          {legacyQuestionDeck ? "Needs regeneration" : `${percent}% covered`}
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p>
          {legacyQuestionDeck
            ? "This deck predates source-item coverage checks. Regenerate it to audit every exercise and worked example."
            : sourceItemCoverage
              ? `${sourceCovered} of ${sourceTotal} exercises and worked examples have complete cards.`
              : questionCoverage
                ? `${sourceCovered} of ${sourceTotal} source questions have complete cards.`
              : `${metrics.topics_covered} of ${metrics.topics_required} required topics have at least one card.`}
        </p>
        {uncovered.length > 0 ? (
          <p className="mt-1 text-xs opacity-80">
            Missing:{" "}
            {uncovered.slice(0, 3).join("; ")}
            {uncovered.length > 3 ? "…" : ""}
          </p>
        ) : null}
      </TooltipContent>
    </Tooltip>
  );
}

function updatedLabel(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(date);
}

export function DeckRow({ deck }: { deck: DeckSummary }) {
  const Icon =
    deck.document_type === "paper"
      ? FileText
      : deck.source_kind === "book"
        ? BookOpen
        : Video;
  const percent = coveragePercent(deck.metrics);
  const extracted = deck.generation_mode === "book_extracted";

  return (
    <li className="group border-b border-border px-3 py-3 last:border-b-0 hover:bg-surface-hover focus-within:bg-surface-hover sm:grid sm:grid-cols-[minmax(0,2.6fr)_minmax(6rem,1.1fr)_3.5rem_5rem_7.75rem_5.5rem_4.5rem] sm:items-center sm:gap-3 sm:px-4">
      <div className="min-w-0">
        <Link
          href={`/decks/${deck.deck_id}`}
          className="flex min-w-0 items-start gap-3 rounded-sm"
        >
          <Icon aria-hidden className="mt-1 size-4 shrink-0 text-primary" />
          <span className="min-w-0">
            <span className="block truncate font-serif text-sm font-medium">
              {deck.title}
              {!extracted ? (
                <span className="ml-2 font-sans text-xs font-normal text-muted-foreground">
                  Set {deck.set_number}
                </span>
              ) : null}
            </span>
            <span className="mt-1 block truncate text-xs text-muted-foreground sm:hidden">
              {deck.source_title}
            </span>
          </span>
        </Link>
      </div>

      <p className="hidden truncate text-xs text-muted-foreground sm:block">
        {deck.source_title}
      </p>
      <p className="hidden text-sm tabular-nums sm:block">{deck.card_count}</p>
      <div className="hidden sm:block">
        <div className="flex items-center justify-between text-xs tabular-nums">
          <span>{percent}%</span>
        </div>
        <Progress className="mt-2 h-0.5" value={percent} />
      </div>
      <div className="hidden items-center gap-1 text-xs tabular-nums sm:flex">
        {deck.due_count > 0 ? (
          <Badge className="font-normal">{deck.due_count} due</Badge>
        ) : null}
        <span className="text-muted-foreground">
          {deck.due_count > 0 && deck.new_count > 0 ? "/ " : ""}
          {deck.new_count > 0 ? `${deck.new_count} new` : "Caught up"}
        </span>
      </div>
      <p className="hidden text-xs text-muted-foreground sm:block">
        {updatedLabel(deck.updated_at)}
      </p>
      <Button asChild variant="outline" size="sm" className="hidden sm:flex">
        <Link href={`/decks/${deck.deck_id}`}>Open</Link>
      </Button>

      <div className="mt-2 flex flex-wrap items-center gap-2 pl-6 sm:hidden">
        <Badge variant={extracted ? "secondary" : "outline"} className="gap-1 font-normal">
          {extracted ? <BookOpen aria-hidden className="size-3" /> : <Sparkles aria-hidden className="size-3" />}
          {extracted ? "Exercises & examples" : "AI-generated"}
        </Badge>
        <Badge variant="outline" className="font-normal tabular-nums">
          {deck.card_count} cards
        </Badge>
        <CoverageBadge metrics={deck.metrics} generationMode={deck.generation_mode} />
        {deck.due_count > 0 ? <Badge>{deck.due_count} due</Badge> : null}
        {deck.new_count > 0 ? <Badge variant="secondary">{deck.new_count} new</Badge> : null}
      </div>
      {deck.metrics.notice ? (
        <p className="col-span-full mt-2 pl-6 text-xs font-medium text-warning dark:text-warning">
          {deck.metrics.notice}
        </p>
      ) : null}
    </li>
  );
}

function StageIcon({ state }: { state: "done" | "active" | "pending" }) {
  if (state === "done") {
    return (
      <span className="grid size-5 place-items-center rounded-full bg-primary text-primary-foreground">
        <Check aria-hidden className="size-3" />
      </span>
    );
  }
  if (state === "active") {
    return <LoaderCircle aria-hidden className="size-5 animate-spin text-primary" />;
  }
  return <Circle aria-hidden className="size-5 text-muted-foreground" />;
}

/** One live or failed generation, with the same observable detail as ingestion. */
export function DeckJobRow({
  job,
  onRetry,
  onCancel,
  onDismiss,
}: {
  job: DeckJob;
  onRetry?: (job: DeckJob) => void;
  onCancel?: (job: DeckJob) => void;
  onDismiss?: (job: DeckJob) => void;
}) {
  const live = jobIsLive(job);
  const failed = job.status === "failed";
  const extracted = job.generation_mode === "book_extracted";
  const failure = deckJobError(job);
  const [receivedAt, setReceivedAt] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const snapshot = Date.now();
    setReceivedAt(snapshot);
    setNow(snapshot);
  }, [job.timing.elapsed_seconds, job.updated_at]);

  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [live]);

  const sincePoll = live ? Math.max(0, (now - receivedAt) / 1_000) : 0;
  const elapsed = job.timing.elapsed_seconds + sincePoll;
  const remaining =
    job.timing.estimated_remaining_seconds == null
      ? null
      : Math.max(0, job.timing.estimated_remaining_seconds - sincePoll);
  const itemLabel = extracted ? "source items answered" : "topics completed";

  return (
    <li
      className={cn(
        "rounded-lg border bg-surface p-4",
        failed && "border-destructive bg-destructive-wash",
      )}
      aria-live={live ? "polite" : undefined}
    >
      <div className="flex items-start gap-3">
        {failed ? (
          <AlertCircle aria-hidden className="mt-1 size-4 shrink-0 text-destructive" />
        ) : (
          <BookOpen aria-hidden className="mt-1 size-4 shrink-0 text-muted-foreground" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <Badge
                  variant={extracted ? "secondary" : "outline"}
                  className="font-normal"
                >
                  {extracted ? "Exercises & examples" : "AI-generated"}
                </Badge>
                <Badge
                  variant={failed ? "destructive" : "outline"}
                  className="font-normal"
                >
                  {failed ? "Failed" : job.status === "queued" ? "Queued" : "In progress"}
                </Badge>
              </div>
              <p className="truncate font-serif text-sm font-medium">{job.title}</p>
              <p className="truncate text-xs text-muted-foreground">{job.source_title}</p>
            </div>
            {!failed ? (
              <span className="text-xl font-medium tabular-nums">
                {Math.round(job.timing.percent)}%
              </span>
            ) : null}
          </div>

          {failed ? (
            <div className="mt-3">
              <p className="text-sm font-medium">{failure.title}</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {failure.message}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {onRetry ? (
                  <Button type="button" size="sm" onClick={() => onRetry(job)}>
                    Try again
                  </Button>
                ) : null}
                {onDismiss ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => onDismiss(job)}
                  >
                    <X aria-hidden />
                    Dismiss
                  </Button>
                ) : null}
                <details className="group/details">
                  <summary className="cursor-pointer rounded-md border border-border px-3 py-2 text-xs text-muted-foreground hover:bg-accent">
                    Technical details
                  </summary>
                  <div className="mt-2 rounded-md bg-surface p-3 text-xs leading-5 text-muted-foreground">
                    <p>Reference: {failure.reference}</p>
                    <p>Code: {job.error_code || "generation_stopped"}</p>
                    {job.error_detail ? (
                      <p className="mt-1 break-words font-mono text-xs">
                        {job.error_detail}
                      </p>
                    ) : null}
                  </div>
                </details>
              </div>
            </div>
          ) : (
            <>
              <Progress
                className="mt-3 h-1.5"
                value={job.timing.percent}
                aria-label={`${Math.round(job.timing.percent)} percent complete`}
              />
              <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-b border-border pb-3 text-xs sm:grid-cols-4">
                <div>
                  <p className="font-medium tabular-nums">
                    {job.topics_total > 0
                      ? `${job.topics_done} of ${job.topics_total}`
                      : "Preparing"}
                  </p>
                  <p className="text-muted-foreground">{itemLabel}</p>
                </div>
                <div>
                  <p className="font-medium tabular-nums">{formatDeckDuration(elapsed)}</p>
                  <p className="text-muted-foreground">elapsed</p>
                </div>
                <div>
                  <p className="font-medium tabular-nums">
                    {remaining == null
                      ? "Re-estimating"
                      : remaining < 10
                        ? "Finishing up"
                        : `About ${formatDeckDuration(remaining)}`}
                  </p>
                  <p className="text-muted-foreground">estimated time left</p>
                </div>
                <p className="self-end text-muted-foreground">
                  You can close this page safely.
                </p>
              </div>
              <ol className="mt-3 space-y-3">
                {job.timing.stages.map((stage) => (
                  <li key={stage.stage} className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-2 text-xs">
                    <StageIcon state={stage.state} />
                    <span className={cn(stage.state === "pending" && "text-muted-foreground")}>
                      {stage.label}
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {stage.state === "done"
                        ? "Completed"
                        : stage.state === "active"
                          ? "In progress"
                          : `~${formatDeckDuration(stage.expected_seconds)}`}
                    </span>
                  </li>
                ))}
              </ol>
              {onCancel ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="mt-3 text-muted-foreground"
                  onClick={() => onCancel(job)}
                >
                  Cancel generation
                </Button>
              ) : null}
            </>
          )}
        </div>
      </div>
    </li>
  );
}
