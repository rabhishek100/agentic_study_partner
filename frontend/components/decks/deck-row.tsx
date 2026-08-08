"use client";

import { AlertTriangle, BookOpen, Loader2, Video } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
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
} from "@/lib/deck-types";

/**
 * Coverage, stated as a number.
 *
 * Every other study app asks you to trust that a generated deck covered the
 * chapter. This one compared the cards against a topic list built from the
 * canonical outline before any model call, so it can say how many topics were
 * reached and name the ones that were not.
 */
export function CoverageBadge({ metrics }: { metrics: DeckMetrics }) {
  const percent = coveragePercent(metrics);
  const complete = metrics.topics_covered >= metrics.topics_required;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          variant={complete ? "secondary" : "outline"}
          className="gap-1 font-normal tabular-nums"
        >
          {!complete ? <AlertTriangle aria-hidden className="size-3" /> : null}
          {percent}% covered
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p>
          {metrics.topics_covered} of {metrics.topics_required} required topics
          have at least one card.
        </p>
        {metrics.uncovered_topic_labels.length > 0 ? (
          <p className="mt-1 text-xs opacity-80">
            Missing: {metrics.uncovered_topic_labels.slice(0, 3).join("; ")}
            {metrics.uncovered_topic_labels.length > 3 ? "…" : ""}
          </p>
        ) : null}
      </TooltipContent>
    </Tooltip>
  );
}

export function DeckRow({ deck }: { deck: DeckSummary }) {
  const Icon = deck.source_kind === "book" ? BookOpen : Video;

  return (
    <li>
      <Link
        href={`/decks/${deck.deck_id}`}
        className="block rounded-lg border border-border bg-card p-4 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-start gap-3">
          <Icon aria-hidden className="mt-0.5 size-4 shrink-0 opacity-70" />
          <div className="min-w-0 flex-1">
            <p className="truncate font-heading text-sm font-medium">
              {deck.title}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {deck.source_title}
            </p>

            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="font-normal tabular-nums">
                {deck.card_count} cards
              </Badge>
              <CoverageBadge metrics={deck.metrics} />
              {deck.due_count > 0 ? (
                <Badge className="font-normal tabular-nums">
                  {deck.due_count} due
                </Badge>
              ) : null}
              {deck.new_count > 0 ? (
                <Badge variant="secondary" className="font-normal tabular-nums">
                  {deck.new_count} new
                </Badge>
              ) : null}
            </div>
          </div>
        </div>
      </Link>
    </li>
  );
}

/** A generation still in flight, shown where its deck will appear. */
export function DeckJobRow({ job }: { job: DeckJob }) {
  const failed = job.status === "failed";

  return (
    <li className="rounded-lg border border-dashed border-border p-4">
      <div className="flex items-start gap-3">
        {failed ? (
          <AlertTriangle aria-hidden className="mt-0.5 size-4 text-destructive" />
        ) : (
          <Loader2 aria-hidden className="mt-0.5 size-4 animate-spin opacity-70" />
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {failed ? "Generation failed" : "Making cards…"}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {failed
              ? job.error_detail || job.error_code || "Try generating it again."
              : job.stage === "generation" && job.topics_total > 0
                ? `${job.topics_done} of ${job.topics_total} topics`
                : job.stage.replace(/_/g, " ")}
          </p>
          {!failed ? (
            <Progress className="mt-2" value={job.progress * 100} />
          ) : null}
        </div>
      </div>
    </li>
  );
}
