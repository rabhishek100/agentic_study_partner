"use client";

import {
  BookOpen,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Eye,
  MessageSquare,
  Video,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CardBackFace,
  CardFront,
  CardSources,
  SourceItemMeta,
  isSourceAuthoredCard,
  solutionLabel,
} from "@/components/decks/card-face";
import { AskSelection } from "@/components/side-chat/ask-selection";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { apiFetch } from "@/lib/api";
import { questionForCard, stashQuestion } from "@/lib/deck-handoff";
import {
  RATING_LABELS,
  type McqOption,
  type QueueCard,
  type Rating,
  type ReviewState,
  describeInterval,
} from "@/lib/deck-types";
import { cn } from "@/lib/utils";

interface GradeResponse {
  card_id: string;
  review: ReviewState;
}

/** What the session remembers about one card while you move around it. */
interface CardState {
  revealed: boolean;
  questionCollapsed: boolean;
  selected: McqOption["label"] | null;
  /** Set once graded in this session; a revisit shows it and can change it. */
  rating: Rating | null;
  /** The schedule the server actually returned, for the revisit summary. */
  review: ReviewState | null;
}

const RATINGS: Rating[] = [1, 2, 3, 4];

function timestampLabel(ms: number): string {
  const total = Math.floor(ms / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

const EMPTY_STATE: CardState = {
  revealed: false,
  questionCollapsed: false,
  selected: null,
  rating: null,
  review: null,
};

/**
 * What each button costs you, shown on the button.
 *
 * Anki hides this behind a setting and it is the single most useful thing on
 * the screen: "Good" meaning nine days and "Hard" meaning three is the whole
 * decision, and without it the four buttons are guesswork.
 */
function nextIntervalHint(state: ReviewState, rating: Rating): string {
  const base = Math.max(state.interval_days, 1);
  if (state.state === "new" || state.state === "learning") {
    if (rating === 4) return describeInterval(4);
    // Good graduates: grading happens on the same view that introduced the
    // card, so it has already served its learning step by the time you press.
    if (rating === 3) return describeInterval(1);
    return "10 min";
  }
  if (rating === 1) return "10 min";
  if (rating === 2) return describeInterval(base * 1.2);
  if (rating === 3) return describeInterval(base * state.ease);
  return describeInterval(base * state.ease * 1.3);
}

export function ReviewSession({
  cards,
  onFinished,
  onExit,
  onAskSelection,
  sourceQuestions = false,
  initialCardId = null,
}: {
  cards: QueueCard[];
  onFinished?: () => void;
  onExit?: () => void;
  /** The deck contains exercises and worked examples from the source book. */
  sourceQuestions?: boolean;
  /** Opens a selected browse card directly; defaults to the first due card. */
  initialCardId?: string | null;
  /**
   * Highlighted text on the back of a card, handed up to be asked about.
   *
   * The window layer belongs to the page, not to this component: a side chat
   * floats against the viewport, so nesting it inside the review column would
   * clip it at that column's edge — the same reason the chat surfaces mount it
   * outside their scrolling layout.
   */
  onAskSelection?: (card: QueueCard, quotedText: string) => void;
}) {
  const router = useRouter();
  const backRef = useRef<HTMLDivElement | null>(null);
  // Ranges over the cards plus one past the end, which is the summary slate.
  // Making the summary a position rather than a separate mode is what lets
  // Previous walk back into the deck after the last card is graded.
  const [index, setIndex] = useState(() => {
    if (!initialCardId) return 0;
    const selected = cards.findIndex(
      (item) => item.card.card_id === initialCardId,
    );
    return selected >= 0 ? selected : 0;
  });
  const [states, setStates] = useState<Record<string, CardState>>({});
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [overviewOpen, setOverviewOpen] = useState(false);
  const shownAt = useRef<number>(Date.now());

  const item = cards[index];
  const cardId = item?.card.card_id ?? "";
  const state = states[cardId] ?? EMPTY_STATE;
  const atSummary = index >= cards.length;

  /**
   * How many distinct cards this session graded.
   *
   * Counted from ids rather than by filtering the current list: grading a card
   * takes it out of the due queue, so a refetch mid-session would drop it from
   * `cards` and the tally would report fewer reviews than actually happened.
   * It reported zero after a real review, which is how this was found.
   */
  const gradedIds = useMemo(
    () =>
      new Set(
        Object.entries(states)
          .filter(([, entry]) => entry.rating)
          .map(([id]) => id),
      ),
    [states],
  );
  const gradedCount = gradedIds.size;

  const correctLabel = useMemo(
    () => item?.card.back.options.find((option) => option.correct)?.label ?? null,
    [item],
  );

  useEffect(() => {
    shownAt.current = Date.now();
  }, [index]);

  const patch = useCallback(
    (id: string, changes: Partial<CardState>) => {
      setStates((current) => ({
        ...current,
        [id]: { ...(current[id] ?? EMPTY_STATE), ...changes },
      }));
    },
    [],
  );

  const goTo = useCallback(
    (next: number) => {
      setError("");
      setIndex(Math.max(0, Math.min(cards.length, next)));
    },
    [cards.length],
  );

  /**
   * After grading, jump to the next card still needing one.
   *
   * Plain "next" would land you back on a card you graded ten seconds ago
   * whenever you had navigated backwards to re-answer something.
   */
  const advance = useCallback(
    (from: number, graded: Record<string, CardState>) => {
      for (let position = from + 1; position < cards.length; position += 1) {
        if (!graded[cards[position]?.card.card_id ?? ""]?.rating) {
          setIndex(position);
          return;
        }
      }
      setIndex(cards.length);
    },
    [cards],
  );

  const grade = useCallback(
    async (rating: Rating) => {
      if (!item?.card.card_id || pending) return;
      const id = item.card.card_id;
      setPending(true);
      setError("");
      try {
        const response = await apiFetch<GradeResponse>(
          `/decks/cards/${id}/review`,
          {
            method: "POST",
            body: JSON.stringify({
              rating,
              elapsed_ms: Date.now() - shownAt.current,
              answered_correctly:
                item.card.card_type === "mcq" && state.selected
                  ? state.selected === correctLabel
                  : null,
            }),
          },
        );
        const updated = {
          ...states,
          [id]: {
            ...(states[id] ?? EMPTY_STATE),
            revealed: true,
            rating,
            review: response.review,
          },
        };
        setStates(updated);
        advance(index, updated);
      } catch (caught) {
        // The card stays on screen. Advancing after a failed write would
        // silently drop the review and reschedule nothing.
        setError(
          (caught as Error).message || "That review could not be recorded.",
        );
      } finally {
        setPending(false);
      }
    },
    [advance, correctLabel, index, item, pending, state.selected, states],
  );

  const askAboutCard = useCallback(() => {
    if (!item) return;
    stashQuestion({
      question: questionForCard(item.card.front, item.deck_title),
      bookIds: item.book_id ? [item.book_id] : undefined,
    });
    router.push(item.video_id ? `/videos/${item.video_id}` : "/");
  }, [item, router]);

  const openSource = useCallback(() => {
    if (!item) return;
    const citation = item.card.citations[0];
    if (!citation) return;
    if (item.video_id && citation.start_ms !== null) {
      router.push(
        `/videos/${item.video_id}?t=${Math.floor(citation.start_ms / 1000)}`,
      );
      return;
    }
    if (item.book_id && citation.page !== null) {
      router.push(`/?book=${item.book_id}&page=${citation.page}`);
    }
  }, [item, router]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;

      if (event.key === "ArrowLeft") {
        event.preventDefault();
        goTo(index - 1);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        goTo(index + 1);
        return;
      }
      if (atSummary || !cardId) return;

      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        if (!state.revealed) patch(cardId, { revealed: true });
        else void grade(3);
        return;
      }
      if (!state.revealed) {
        // Before the reveal the digits pick an MCQ option, because that is
        // what a number means on that screen. After it they grade.
        const option = ["1", "2", "3", "4"].indexOf(event.key);
        if (option >= 0 && item?.card.card_type === "mcq") {
          patch(cardId, {
            selected: item.card.back.options[option]?.label ?? null,
          });
        }
        return;
      }
      const rating = RATINGS.find((value) => String(value) === event.key);
      if (rating) {
        event.preventDefault();
        void grade(rating);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [atSummary, cardId, goTo, grade, index, item, patch, state.revealed]);

  /**
   * Leave, then let the page behind refresh its counts.
   *
   * Refreshing on reaching the summary instead would swap the card list out
   * from under a session you can still navigate backwards into — a graded card
   * is no longer due, so it disappears and the arrows land somewhere else.
   */
  const finish = useCallback(() => {
    onFinished?.();
    onExit?.();
  }, [onExit, onFinished]);

  if (cards.length === 0) {
    return (
      <div className="mx-auto max-w-md rounded-xl border border-dashed border-border p-12 text-center">
        <Check aria-hidden className="mx-auto mb-3 size-6 text-positive" />
        <p className="text-base font-medium">Nothing due</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything scheduled for today is done. New cards appear as your
          daily allowance frees up.
        </p>
        <Button className="mt-6" variant="outline" onClick={finish}>
          Back to the decks
        </Button>
      </div>
    );
  }

  const overview = (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={() => setOverviewOpen((open) => !open)}
          aria-expanded={overviewOpen}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline"
        >
          {overviewOpen ? "Hide all cards" : `See all ${cards.length} cards`}
        </button>
        <span className="text-xs text-muted-foreground tabular-nums">
          {gradedCount} graded
        </span>
      </div>
      {overviewOpen ? (
        <ul className="flex max-h-40 flex-wrap gap-1 overflow-y-auto rounded-lg border border-border p-2">
          {cards.map((entry, position) => {
            const entryState = states[entry.card.card_id ?? ""];
            return (
              <li key={entry.card.card_id ?? position}>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      onClick={() => goTo(position)}
                      aria-current={position === index ? "true" : undefined}
                      className={cn(
                        "size-7 rounded-md border text-xs tabular-nums transition-colors",
                        "",
                        position === index
                          ? "border-primary bg-primary text-primary-foreground"
                          : entryState?.rating
                            ? "border-transparent bg-accent text-accent-foreground"
                            : "border-border text-muted-foreground hover:bg-surface-hover",
                      )}
                    >
                      {position + 1}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">
                    <p>{entry.card.front}</p>
                    {entryState?.rating ? (
                      <p className="mt-1 text-xs opacity-80">
                        Graded {RATING_LABELS[entryState.rating]}
                      </p>
                    ) : null}
                  </TooltipContent>
                </Tooltip>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );

  if (atSummary) {
    return (
      <div className="mx-auto flex w-full max-w-2xl flex-col gap-4">
        <div className="rounded-xl border border-border bg-card p-12 text-center">
          <Check aria-hidden className="mx-auto mb-3 size-6 text-positive" />
          <p className="font-serif text-lg font-medium">
            {gradedCount} of {cards.length} card
            {cards.length === 1 ? "" : "s"} reviewed
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {gradedCount === cards.length
              ? "The ones you struggled with are already scheduled to come back sooner."
              : "The rest are still waiting — step back to reach them."}
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <Button variant="outline" onClick={() => goTo(cards.length - 1)}>
              <ChevronLeft aria-hidden />
              Back to the cards
            </Button>
            <Button onClick={finish}>Done</Button>
          </div>
        </div>
        {overview}
      </div>
    );
  }

  // `atSummary` already covers running off the end; this narrows `item` for
  // the render rather than guarding a state the component can reach.
  if (!item) return null;

  const firstCitation = item.card.citations[0];
  const sourceLocation = firstCitation?.page
    ? `p. ${firstCitation.page}`
    : firstCitation?.start_ms !== null && firstCitation?.start_ms !== undefined
      ? timestampLabel(firstCitation.start_ms)
      : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-background">
      <header className="shrink-0 border-b border-border bg-canvas px-4 py-3 backdrop-blur sm:px-6">
        <div className="mx-auto grid w-full max-w-6xl grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-4">
          <Button variant="ghost" size="sm" onClick={finish}>
            <ChevronLeft aria-hidden />
            <span className="hidden sm:inline">Back to deck</span>
          </Button>

          <div className="mx-auto w-full max-w-3xl space-y-2">
            <div className="flex min-w-0 items-center gap-2 text-sm">
              <span className="truncate font-serif font-medium">
                {item.deck_title}
              </span>
              <span className="shrink-0 rounded-md bg-muted px-2 py-1 text-xs tabular-nums text-muted-foreground">
                {index + 1} / {cards.length}
              </span>
            </div>
            <Progress value={((index + 1) / cards.length) * 100} />
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => goTo(index - 1)}
              disabled={index === 0}
              aria-label="Previous card"
            >
              <ChevronLeft aria-hidden />
              <span className="hidden md:inline">Previous</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => goTo(index + 1)}
              aria-label="Next card"
            >
              <span className="hidden md:inline">Next</span>
              <ChevronRight aria-hidden />
            </Button>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <article className="mx-auto w-full max-w-4xl px-6 py-6 sm:px-8 sm:py-8">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 text-xs">
            <span className="font-medium text-primary">
              {sourceQuestions ? "Source item" : "Card"} {index + 1}
            </span>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground">
              <span className="flex items-center gap-1">
                {item.source_kind === "book" ? (
                  <BookOpen aria-hidden className="size-3.5" />
                ) : (
                  <Video aria-hidden className="size-3.5" />
                )}
                {item.source_title}
                {sourceLocation ? ` · ${sourceLocation}` : ""}
              </span>
              <span aria-hidden>•</span>
              {isSourceAuthoredCard(item.card) ? (
                <>
                  <SourceItemMeta card={item.card} />
                  <span aria-hidden>•</span>
                  <span>{solutionLabel(item.card)}</span>
                </>
              ) : (
                <span>Grounded in source</span>
              )}
              <span aria-hidden>•</span>
              <span>{item.card.difficulty}</span>
              {state.rating ? (
                <>
                  <span aria-hidden>•</span>
                  <span>
                    Graded {RATING_LABELS[state.rating]}
                    {state.review
                      ? ` · back in ${describeInterval(state.review.interval_days)}`
                      : ""}
                  </span>
                </>
              ) : null}
            </div>
          </div>

          {!state.questionCollapsed ? (
            <CardFront
              card={item.card}
              selected={state.selected}
              studyMode
              onSelect={
                state.revealed
                  ? undefined
                  : (label) => patch(cardId, { selected: label })
              }
            />
          ) : null}

          {state.revealed ? (
            <>
              <div className="my-6 flex items-center gap-3">
                <div className="h-px flex-1 bg-border" />
                <button
                  type="button"
                  aria-expanded={!state.questionCollapsed}
                  onClick={() =>
                    patch(cardId, {
                      questionCollapsed: !state.questionCollapsed,
                    })
                  }
                  className="flex items-center gap-2 rounded-md px-2 py-1 text-sm text-primary hover:bg-accent"
                >
                  <ChevronDown
                    aria-hidden
                    className={cn(
                      "size-4 transition-transform",
                      !state.questionCollapsed && "rotate-180",
                    )}
                  />
                  Question ({state.questionCollapsed ? "expand" : "collapse"})
                </button>
                <div className="h-px flex-1 bg-border" />
              </div>

              {/*
                `data-turn-index` and `data-answer` are what the selection
                reader looks for. A card is one answer, so the index remains
                constant while the server owns the eventual conversation turn.
              */}
              <div
                ref={backRef}
                data-turn-index="0"
                data-answer
                className="space-y-6"
              >
                <CardBackFace
                  item={item}
                  selected={state.selected}
                  studyMode
                />
                <CardSources item={item} onOpenSource={openSource} />
              </div>
            </>
          ) : null}

          {error ? (
            <p role="alert" className="mt-6 text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </article>
      </div>

      {state.revealed && onAskSelection && item.source_kind === "book" ? (
        <AskSelection
          container={backRef}
          onAsk={(_turnIndex, quotedText) => onAskSelection(item, quotedText)}
        />
      ) : null}

      <footer className="shrink-0 border-t border-border bg-canvas px-4 py-3 backdrop-blur sm:px-6">
        {!state.revealed ? (
          <div className="mx-auto flex w-full max-w-3xl justify-center">
            <Button
              size="lg"
              className="min-w-56"
              onClick={() => patch(cardId, { revealed: true })}
            >
              <Eye aria-hidden />
              Show answer
              <kbd className="ml-2 rounded border border-divider px-2 text-xs opacity-70">
                space
              </kbd>
            </Button>
          </div>
        ) : (
          <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 lg:flex-row lg:items-center">
            <div className="flex shrink-0 flex-wrap items-center gap-1 lg:min-w-[22rem]">
              <Button variant="outline" size="sm" onClick={askAboutCard}>
                <MessageSquare aria-hidden />
                Ask about this
              </Button>
              {item.card.citations.length > 0 ? (
                <Button variant="outline" size="sm" onClick={openSource}>
                  {item.source_kind === "book" ? (
                    <BookOpen aria-hidden />
                  ) : (
                    <Video aria-hidden />
                  )}
                  Open source
                </Button>
              ) : null}
            </div>

            <div className="hidden h-10 w-px bg-border lg:block" />
            <div className="min-w-32 shrink-0">
              <p className="text-xs font-medium">How well did you recall this?</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Choose one rating
              </p>
            </div>
            <div className="grid min-w-0 flex-1 grid-cols-4 gap-2">
              {RATINGS.map((rating) => (
                <Button
                  key={rating}
                  variant="outline"
                  disabled={pending}
                  onClick={() => void grade(rating)}
                  className={cn(
                    "h-auto min-w-0 flex-col gap-1 py-2",
                    rating === 1 && "border-destructive text-destructive",
                    rating === 2 && "border-evidence text-citation",
                    rating === 3 && "border-action text-primary",
                    rating === 4 && "border-positive text-positive",
                    state.rating === rating && "bg-accent ring-1 ring-divider",
                  )}
                >
                  <span className="text-sm font-medium">
                    {RATING_LABELS[rating]}
                  </span>
                  <span className="text-xs font-normal opacity-70 tabular-nums">
                    {nextIntervalHint(state.review ?? item.review, rating)}
                  </span>
                </Button>
              ))}
            </div>
          </div>
        )}
        <p className="sr-only">
          1–4 to grade, space for Good, left and right arrows to move between
          cards.
        </p>
      </footer>
    </div>
  );
}
