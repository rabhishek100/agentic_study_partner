"use client";

import {
  BookOpen,
  Check,
  ChevronLeft,
  ChevronRight,
  Eye,
  MessageSquare,
  RotateCcw,
  Video,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CardBackFace,
  CardFront,
  CardMeta,
  CardSources,
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
  selected: McqOption["label"] | null;
  /** Set once graded in this session; a revisit shows it and can change it. */
  rating: Rating | null;
  /** The schedule the server actually returned, for the revisit summary. */
  review: ReviewState | null;
}

const RATINGS: Rating[] = [1, 2, 3, 4];

const EMPTY_STATE: CardState = {
  revealed: false,
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
}: {
  cards: QueueCard[];
  onFinished?: () => void;
  onExit?: () => void;
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
  const [index, setIndex] = useState(0);
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
      <div className="mx-auto max-w-md rounded-xl border border-dashed border-border p-10 text-center">
        <Check aria-hidden className="mx-auto mb-3 size-6 text-emerald-600" />
        <p className="font-heading text-base font-medium">Nothing due</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything scheduled for today is done. New cards appear as your
          daily allowance frees up.
        </p>
        <Button className="mt-5" variant="outline" onClick={finish}>
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
          className="text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
                        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        position === index
                          ? "border-primary bg-primary text-primary-foreground"
                          : entryState?.rating
                            ? "border-transparent bg-accent text-accent-foreground"
                            : "border-border text-muted-foreground hover:bg-accent/60",
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
        <div className="rounded-xl border border-border bg-card p-10 text-center">
          <Check aria-hidden className="mx-auto mb-3 size-6 text-emerald-600" />
          <p className="font-heading text-lg font-medium">
            {gradedCount} of {cards.length} card
            {cards.length === 1 ? "" : "s"} reviewed
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            {gradedCount === cards.length
              ? "The ones you struggled with are already scheduled to come back sooner."
              : "The rest are still waiting — step back to reach them."}
          </p>
          <div className="mt-5 flex justify-center gap-2">
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

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-4">
      <div className="space-y-1.5">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            {item.source_kind === "book" ? (
              <BookOpen aria-hidden className="size-3.5" />
            ) : (
              <Video aria-hidden className="size-3.5" />
            )}
            <span className="truncate">{item.deck_title}</span>
          </span>
          <span className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => goTo(index - 1)}
              disabled={index === 0}
              aria-label="Previous card"
            >
              <ChevronLeft aria-hidden />
            </Button>
            <span className="tabular-nums">
              {index + 1} / {cards.length}
            </span>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={() => goTo(index + 1)}
              aria-label="Next card"
            >
              <ChevronRight aria-hidden />
            </Button>
          </span>
        </div>
        <Progress value={(gradedCount / cards.length) * 100} />
      </div>

      <div className="rounded-xl border border-border bg-card p-5 sm:p-7">
        <div className="mb-4 flex flex-wrap items-center gap-1.5">
          <CardMeta card={item.card} />
          {state.rating ? (
            <span className="text-xs text-muted-foreground">
              graded {RATING_LABELS[state.rating]}
              {state.review
                ? ` · back in ${describeInterval(state.review.interval_days)}`
                : ""}
            </span>
          ) : null}
        </div>

        <CardFront
          card={item.card}
          selected={state.selected}
          onSelect={
            state.revealed
              ? undefined
              : (label) => patch(cardId, { selected: label })
          }
        />

        {state.revealed ? (
          /*
            `data-turn-index` and `data-answer` are what the selection reader
            looks for. A card is one answer, so the index is a constant here —
            which turn it really becomes is the server's decision, taken when
            the card is first asked about.
          */
          <div
            ref={backRef}
            data-turn-index="0"
            data-answer
            className="mt-6 space-y-4 border-t border-border pt-5"
          >
            <CardBackFace item={item} selected={state.selected} />
            <CardSources item={item} onOpenSource={openSource} />
          </div>
        ) : null}
      </div>

      {state.revealed && onAskSelection && item.source_kind === "book" ? (
        <AskSelection
          container={backRef}
          onAsk={(_turnIndex, quotedText) => onAskSelection(item, quotedText)}
        />
      ) : null}

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {!state.revealed ? (
        <Button size="lg" onClick={() => patch(cardId, { revealed: true })}>
          <Eye aria-hidden />
          Show answer
          <kbd className="ml-2 rounded border border-current/30 px-1.5 text-xs opacity-70">
            space
          </kbd>
        </Button>
      ) : (
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {RATINGS.map((rating) => (
              <Button
                key={rating}
                // Filled means "this is what you pressed", falling back to
                // Good as the suggested answer on a card not yet graded.
                variant={(state.rating ?? 3) === rating ? "default" : "outline"}
                disabled={pending}
                onClick={() => void grade(rating)}
                className={cn(
                  "h-auto flex-col gap-0.5 py-2.5",
                  rating === 1 &&
                    state.rating !== 1 &&
                    "border-destructive/50 text-destructive",
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

          {/*
            The two things worth doing with a card you just failed. Both reuse
            what already exists: the document viewer, and grounded chat.
          */}
          <div className="flex flex-wrap justify-center gap-2">
            <Button variant="ghost" size="sm" onClick={askAboutCard}>
              <MessageSquare aria-hidden />
              Ask about this
            </Button>
            {item.card.citations.length > 0 ? (
              <Button variant="ghost" size="sm" onClick={openSource}>
                {item.source_kind === "book" ? (
                  <BookOpen aria-hidden />
                ) : (
                  <Video aria-hidden />
                )}
                Open the source
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => patch(cardId, { revealed: false })}
            >
              <RotateCcw aria-hidden />
              Hide
            </Button>
          </div>

          <p className="text-center text-xs text-muted-foreground">
            1–4 to grade, space for Good, ← → to move between cards.
          </p>
        </div>
      )}

      {overview}
    </div>
  );
}
