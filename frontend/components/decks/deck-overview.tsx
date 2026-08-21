"use client";

import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ExternalLink,
  Loader2,
  MoreVertical,
  Plus,
  RefreshCw,
  RotateCcw,
  Timer,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import {
  CardBackFace,
  CardSources,
  plain,
  sourceQuestion,
} from "@/components/decks/card-face";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type {
  DeckCitation,
  DeckDetailResponse,
  QueueCard,
} from "@/lib/deck-types";
import { CARD_TYPE_LABELS, describeInterval } from "@/lib/deck-types";
import { cn } from "@/lib/utils";

export type DeckFilter = "all" | "top";

const TOP_PRIORITY = 4;

function cardStatus(item: QueueCard): string {
  return item.review.state === "new"
    ? "New"
    : `Next in ${describeInterval(item.review.interval_days)}`;
}

function questionLabel(item: QueueCard): string {
  return sourceQuestion(item.card.front);
}

export function DeckOverview({
  detail,
  dueNow,
  reviewableCardIds,
  filter,
  generatingSet,
  onFilterChange,
  onStartReview,
  onStudyCard,
  onGenerateSet,
  onReset,
  onOpenSource,
}: {
  detail: DeckDetailResponse;
  dueNow: number;
  /** Cards returned by today's review queue, not every card in the deck. */
  reviewableCardIds: string[];
  filter: DeckFilter;
  generatingSet: boolean;
  onFilterChange: (filter: DeckFilter) => void;
  onStartReview: () => void;
  onStudyCard: (card: QueueCard) => void;
  onGenerateSet: () => void;
  onReset: () => void;
  onOpenSource: (item: QueueCard, citation: DeckCitation) => void;
}) {
  const { deck } = detail;
  const cards = useMemo(() => {
    const all = detail.cards;
    return filter === "top"
      ? all
          .filter((item) => item.card.interview_priority >= TOP_PRIORITY)
          .sort(
            (left, right) =>
              right.card.interview_priority - left.card.interview_priority,
          )
      : all;
  }, [detail.cards, filter]);
  const [selectedCardId, setSelectedCardId] = useState<string | null>(
    detail.cards[0]?.card.card_id ?? null,
  );
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [mobileDetail, setMobileDetail] = useState(false);
  const reviewable = useMemo(
    () => new Set(reviewableCardIds),
    [reviewableCardIds],
  );

  const selected = useMemo(
    () =>
      cards.find((item) => item.card.card_id === selectedCardId) ??
      cards[0] ??
      null,
    [cards, selectedCardId],
  );

  useEffect(() => {
    if (selected?.card.card_id && selected.card.card_id !== selectedCardId) {
      setSelectedCardId(selected.card.card_id);
    }
  }, [selected, selectedCardId]);

  const selectCard = (item: QueueCard) => {
    setSelectedCardId(item.card.card_id);
    if (!window.matchMedia("(min-width: 1024px)").matches) {
      setMobileDetail(true);
    }
  };

  const sourceQuestions = deck.generation_mode === "book_extracted";
  const covered = sourceQuestions
    ? deck.metrics.source_questions_covered
    : deck.metrics.topics_covered;
  const total = sourceQuestions
    ? deck.metrics.source_questions_total
    : deck.metrics.topics_required;
  const exactCoverage = total > 0 ? `${covered}/${total}` : `${deck.card_count}`;
  const selectedIsDue = selected?.card.card_id
    ? reviewable.has(selected.card.card_id)
    : false;

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-background">
      <header className="shrink-0 border-b border-border">
        <div className="flex min-h-20 items-center gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <Button variant="ghost" size="sm" className="shrink-0" asChild>
            <Link href="/decks">
              <ArrowLeft aria-hidden />
              <span className="hidden sm:inline">Back to decks</span>
              <span className="sm:hidden">Back</span>
            </Link>
          </Button>

          <div className="hidden h-8 w-px bg-border md:block" />

          <div className="min-w-0 flex-1 md:pl-2">
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
              <h1 className="truncate font-serif text-lg font-medium sm:text-xl">
                {deck.title}
              </h1>
              {!sourceQuestions ? (
                <span className="rounded-full border border-border px-2 py-1 text-xs leading-none text-muted-foreground">
                  Set {deck.set_number}
                </span>
              ) : null}
              <span className="hidden truncate text-sm text-muted-foreground lg:inline">
                {deck.source_title}
              </span>
              <span aria-hidden className="hidden size-1 rounded-full bg-primary lg:block" />
              <span className="inline-flex items-center gap-2 text-sm">
                <span className="grid size-6 place-items-center rounded-full bg-primary text-primary-foreground">
                  <Check aria-hidden className="size-3.5" />
                </span>
                <span className="tabular-nums">
                  {exactCoverage} {sourceQuestions ? "extracted and answered" : "covered"}
                </span>
              </span>
            </div>
          </div>

          <Button
            size="lg"
            className="hidden h-12 gap-2 px-6 sm:inline-flex"
            disabled={dueNow === 0}
            onClick={onStartReview}
          >
            {dueNow > 0 ? "Start review" : "Nothing due"}
            {dueNow > 0 ? <ArrowRight aria-hidden /> : null}
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Deck options">
                <MoreVertical aria-hidden />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>Deck options</DropdownMenuLabel>
              <DropdownMenuItem
                disabled={generatingSet}
                onSelect={onGenerateSet}
              >
                {generatingSet ? (
                  <Loader2 aria-hidden className="animate-spin" />
                ) : sourceQuestions ? (
                  <RefreshCw aria-hidden />
                ) : (
                  <Plus aria-hidden />
                )}
                {generatingSet
                  ? sourceQuestions
                    ? "Starting regeneration…"
                    : "Starting next set…"
                  : sourceQuestions
                    ? "Regenerate from book"
                    : `Create Set ${deck.set_number + 1}`}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant="destructive"
                onSelect={() => setResetOpen(true)}
              >
                <RotateCcw aria-hidden />
                Reset study progress
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <Collapsible open={coverageOpen} onOpenChange={setCoverageOpen}>
          <div className="border-t border-divider px-4 sm:px-6 lg:px-8">
            <div className="flex min-h-12 items-center gap-2 text-sm text-muted-foreground">
              <BookOpen aria-hidden className="size-4 shrink-0" />
              <span className="truncate">
                Source: {deck.source_title}
              </span>
              <span aria-hidden>·</span>
              <span className="shrink-0">
                Coverage: <span className="text-foreground tabular-nums">{exactCoverage}</span>
              </span>
              <CollapsibleTrigger
                aria-label="Provenance & coverage"
                className="ml-auto inline-flex min-h-9 items-center gap-2 rounded-md px-2 text-foreground hover:bg-accent"
              >
                <span className="hidden sm:inline">Provenance &amp; coverage</span>
                <span className="sm:hidden">Coverage</span>
                <ChevronDown
                  aria-hidden
                  className={cn(
                    "size-4 transition-transform",
                    coverageOpen && "rotate-180",
                  )}
                />
              </CollapsibleTrigger>
            </div>
            <CollapsibleContent className="pb-4">
              <div className="grid gap-3 rounded-lg border border-border bg-surface p-4 text-sm sm:grid-cols-3">
                <div>
                  <p className="text-xs text-muted-foreground">
                    {sourceQuestions ? "Questions found" : "Topics required"}
                  </p>
                  <p className="mt-1 font-medium tabular-nums">{total}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">
                    {sourceQuestions ? "Questions answered" : "Topics covered"}
                  </p>
                  <p className="mt-1 font-medium tabular-nums">{covered}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Cards kept</p>
                  <p className="mt-1 font-medium tabular-nums">
                    {deck.metrics.cards_kept}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Dropped as ungrounded</p>
                  <p className="mt-1 font-medium tabular-nums">
                    {deck.metrics.cards_dropped_uncited +
                      deck.metrics.cards_dropped_out_of_scope}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Dropped as duplicate</p>
                  <p className="mt-1 font-medium tabular-nums">
                    {deck.metrics.cards_dropped_duplicate}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Repair pass</p>
                  <p className="mt-1 font-medium">
                    {deck.metrics.repair_attempted ? "Used" : "Not needed"}
                  </p>
                </div>
              </div>
            </CollapsibleContent>
          </div>
        </Collapsible>
      </header>

      <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[minmax(20rem,34vw)_minmax(0,1fr)] lg:overflow-hidden">
        <aside
          className={cn(
            "border-b border-border lg:min-h-0 lg:border-b-0 lg:border-r",
            mobileDetail && "hidden lg:block",
          )}
        >
          <div className="flex items-center justify-between gap-3 px-4 py-4 sm:px-6">
            <h2 className="font-serif text-lg font-medium">
              {sourceQuestions ? "Chapter exercises" : "Study questions"}
            </h2>
            <div className="flex rounded-lg border border-border p-1" aria-label="Question filter">
              {(["all", "top"] as DeckFilter[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => onFilterChange(value)}
                  aria-pressed={filter === value}
                  className={cn(
                    "min-h-8 rounded-md px-3 text-sm transition-colors",
                    filter === value
                      ? "bg-accent font-medium text-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {value === "all" ? "All" : "Top"}
                </button>
              ))}
            </div>
          </div>

          <nav
            aria-label="Questions in this deck"
            className="max-h-[calc(100dvh-18rem)] overflow-y-auto px-2 pb-2 sm:px-4 lg:max-h-none lg:h-[calc(100%-4.5rem)]"
          >
            {cards.length > 0 ? (
              <ol className="divide-y divide-border">
                {cards.map((item, index) => {
                  const id = item.card.card_id ?? String(item.card.card_index);
                  const active = selected?.card.card_id === item.card.card_id;
                  return (
                    <li key={id}>
                      <button
                        type="button"
                        onClick={() => selectCard(item)}
                        aria-current={active ? "true" : undefined}
                        className={cn(
                          "group relative grid min-h-14 w-full grid-cols-[1.5rem_minmax(0,1fr)_auto] items-center gap-2 rounded-r-lg py-3 pl-3 pr-2 text-left text-sm transition-colors",
                          "",
                          active
                            ? "bg-wash text-foreground before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:rounded-full before:bg-primary"
                            : "hover:bg-surface-hover",
                        )}
                      >
                        <span className="self-start pt-1 font-medium tabular-nums">
                          {index + 1}
                        </span>
                        <span
                          className="line-clamp-2 min-w-0 leading-5"
                          title={questionLabel(item)}
                        >
                          {questionLabel(item)}
                        </span>
                        <span
                          className={cn(
                            "self-start pt-1 text-xs tabular-nums",
                            active ? "text-primary" : "text-muted-foreground",
                          )}
                        >
                          {cardStatus(item)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ol>
            ) : (
              <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted-foreground">
                No high-priority questions are in this deck.
              </div>
            )}
          </nav>
        </aside>

        <main
          className={cn(
            "min-h-0 overflow-y-auto",
            !mobileDetail && "hidden lg:block",
          )}
          aria-live="polite"
        >
          {selected ? (
            <article className="w-full max-w-4xl px-6 py-8 sm:px-8 lg:px-12">
              <Button
                variant="ghost"
                size="sm"
                className="mb-6 lg:hidden"
                onClick={() => setMobileDetail(false)}
              >
                <ArrowLeft aria-hidden />
                All exercises
              </Button>
              <div className="mb-8 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted-foreground">
                <span className="inline-flex items-center gap-2">
                  <BookOpen aria-hidden className="size-4" />
                  {deck.source_title}
                </span>
                <span aria-hidden>·</span>
                <span>
                  {selected.card.answer_source === "printed_in_book"
                    ? "Answer from book"
                    : "Grounded answer"}
                </span>
                <span aria-hidden>·</span>
                <span>{CARD_TYPE_LABELS[selected.card.card_type]}</span>
              </div>

              <p className="mb-6 text-sm font-medium text-muted-foreground">
                {sourceQuestions ? "Exercise" : "Question"} {selected.card.card_index + 1}
              </p>
              <h2 className="whitespace-pre-wrap font-serif text-xl font-normal leading-[1.5] sm:text-lg">
                {sourceQuestion(selected.card.front)}
              </h2>

              <section className="mt-8 border-y border-border py-6" aria-labelledby="answer-preview-heading">
                <h3
                  id="answer-preview-heading"
                  className="flex items-center gap-2 font-serif text-base font-medium text-evidence"
                >
                  <Timer aria-hidden className="size-5" />
                  30-second answer <span className="font-normal">(preview)</span>
                </h3>
                <p className="mt-3 border-l-2 border-evidence py-1 pl-4 font-serif text-base leading-relaxed sm:text-base">
                  {plain(
                    selected.card.back.say_it_aloud || selected.card.back.answer,
                  )}
                </p>

                <div className="mt-6 flex flex-wrap items-center gap-3">
                  <CardSources
                    item={selected}
                    onOpenSource={(citation) => onOpenSource(selected, citation)}
                  />
                  {selected.card.citations.length > 0 ? (
                    <ExternalLink aria-hidden className="size-3.5 text-muted-foreground" />
                  ) : null}
                </div>
              </section>

              <Collapsible className="mt-6">
                <div className="flex flex-wrap items-center gap-3">
                  <Button
                    size="lg"
                    disabled={!selectedIsDue}
                    onClick={() => onStudyCard(selected)}
                  >
                    <BookOpen aria-hidden />
                    {selectedIsDue ? "Study this card" : "Not due today"}
                  </Button>
                  <CollapsibleTrigger asChild>
                    <Button variant="outline" size="lg" className="group">
                      Detailed answer
                      <ChevronDown
                        aria-hidden
                        className="size-4 transition-transform group-data-[state=open]:rotate-180"
                      />
                    </Button>
                  </CollapsibleTrigger>
                </div>
                <CollapsibleContent className="mt-6 border-t border-border pt-6">
                  <CardBackFace item={selected} hideSummary />
                </CollapsibleContent>
              </Collapsible>
            </article>
          ) : null}
        </main>
      </div>

      {!mobileDetail ? (
        <div className="border-t border-border p-3 sm:hidden">
          <Button className="w-full" disabled={dueNow === 0} onClick={onStartReview}>
            {dueNow > 0 ? "Start review" : "Nothing due"}
            {dueNow > 0 ? <ArrowRight aria-hidden /> : null}
          </Button>
        </div>
      ) : null}

      <Dialog open={resetOpen} onOpenChange={setResetOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reset study progress?</DialogTitle>
            <DialogDescription>
              Every card in this deck will return to new. The extracted
              questions and answers will not be changed.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="destructive"
              onClick={() => {
                onReset();
                setResetOpen(false);
              }}
            >
              Reset progress
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
