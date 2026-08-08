"use client";

import { BookOpen, Check, MessageSquare, RotateCcw, Video } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  CardBackFace,
  CardFront,
  CardMeta,
  CardSources,
} from "@/components/decks/card-face";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
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

const RATINGS: Rating[] = [1, 2, 3, 4];

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
}: {
  cards: QueueCard[];
  onFinished?: () => void;
  onExit?: () => void;
}) {
  const router = useRouter();
  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [selected, setSelected] = useState<McqOption["label"] | null>(null);
  const [graded, setGraded] = useState(0);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const shownAt = useRef<number>(Date.now());

  const item = cards[index];
  const finished = index >= cards.length;

  const correctLabel = useMemo(
    () => item?.card.back.options.find((option) => option.correct)?.label ?? null,
    [item],
  );

  useEffect(() => {
    shownAt.current = Date.now();
  }, [index]);

  const advance = useCallback(() => {
    setRevealed(false);
    setSelected(null);
    setIndex((current) => current + 1);
  }, []);

  const grade = useCallback(
    async (rating: Rating) => {
      if (!item?.card.card_id || pending) return;
      setPending(true);
      setError("");
      try {
        await apiFetch<GradeResponse>(
          `/decks/cards/${item.card.card_id}/review`,
          {
            method: "POST",
            body: JSON.stringify({
              rating,
              elapsed_ms: Date.now() - shownAt.current,
              answered_correctly:
                item.card.card_type === "mcq" && selected
                  ? selected === correctLabel
                  : null,
            }),
          },
        );
        setGraded((count) => count + 1);
        advance();
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
    [advance, correctLabel, item, pending, selected],
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
    if (finished) return;
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA"].includes(target.tagName)) return;

      if (event.key === " " || event.key === "Enter") {
        event.preventDefault();
        if (!revealed) setRevealed(true);
        else void grade(3);
        return;
      }
      if (!revealed) {
        // Before the reveal the digits pick an MCQ option, because that is
        // what a number means on that screen. After it they grade.
        const option = ["1", "2", "3", "4"].indexOf(event.key);
        if (option >= 0 && item?.card.card_type === "mcq") {
          setSelected(item.card.back.options[option]?.label ?? null);
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
  }, [finished, grade, item, revealed]);

  useEffect(() => {
    if (finished) onFinished?.();
    // Firing once at the end of the queue; onFinished refetches counts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished]);

  if (cards.length === 0) {
    return (
      <div className="mx-auto max-w-md rounded-xl border border-dashed border-border p-10 text-center">
        <Check aria-hidden className="mx-auto mb-3 size-6 text-emerald-600" />
        <p className="font-heading text-base font-medium">Nothing due</p>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything scheduled for today is done. New cards appear as your
          daily allowance frees up.
        </p>
      </div>
    );
  }

  if (finished) {
    return (
      <div className="mx-auto max-w-md rounded-xl border border-border bg-card p-10 text-center">
        <Check aria-hidden className="mx-auto mb-3 size-6 text-emerald-600" />
        <p className="font-heading text-lg font-medium">
          {graded} card{graded === 1 ? "" : "s"} reviewed
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          The ones you struggled with are already scheduled to come back sooner.
        </p>
        <Button className="mt-5" onClick={onExit}>
          Done
        </Button>
      </div>
    );
  }

  // `finished` already covers the end of the queue; this narrows `item` for
  // the render below rather than guarding a state the component can reach.
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
          <span className="tabular-nums">
            {index + 1} / {cards.length}
          </span>
        </div>
        <Progress value={((index + 1) / cards.length) * 100} />
      </div>

      <div className="rounded-xl border border-border bg-card p-5 sm:p-7">
        <div className="mb-4">
          <CardMeta card={item.card} />
        </div>

        <CardFront
          card={item.card}
          selected={selected}
          onSelect={revealed ? undefined : setSelected}
        />

        {revealed ? (
          <div className="mt-6 space-y-4 border-t border-border pt-5">
            <CardBackFace item={item} selected={selected} />
            <CardSources item={item} onOpenSource={openSource} />
          </div>
        ) : null}
      </div>

      {error ? (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      ) : null}

      {!revealed ? (
        <Button size="lg" onClick={() => setRevealed(true)}>
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
                variant={rating === 3 ? "default" : "outline"}
                disabled={pending}
                onClick={() => void grade(rating)}
                className={cn(
                  "h-auto flex-col gap-0.5 py-2.5",
                  rating === 1 && "border-destructive/50 text-destructive",
                )}
              >
                <span className="text-sm font-medium">
                  {RATING_LABELS[rating]}
                </span>
                <span className="text-xs font-normal opacity-70 tabular-nums">
                  {nextIntervalHint(item.review, rating)}
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
              onClick={() => setRevealed(false)}
            >
              <RotateCcw aria-hidden />
              Hide
            </Button>
          </div>

          <p className="text-center text-xs text-muted-foreground">
            Press 1–4 to grade, or space for Good.
          </p>
        </div>
      )}
    </div>
  );
}
