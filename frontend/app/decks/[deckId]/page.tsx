"use client";

import { ArrowLeft, LogOut } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { CoverageBadge } from "@/components/decks/deck-row";
import {
  CardBackFace,
  CardMeta,
  CardSources,
  plain,
} from "@/components/decks/card-face";
import { ReviewSession } from "@/components/decks/review-session";
import { SectionNav } from "@/components/section-nav";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatTurns } from "@/components/side-chat/side-chat-turns";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { useCardSideChats } from "@/hooks/use-card-side-chats";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn } from "@/lib/types";
import {
  type DeckDetailResponse,
  type QueueCard,
  type ReviewQueue,
  describeInterval,
} from "@/lib/deck-types";

type Filter = "all" | "top";

/** Priority 4 and 5: what an interviewer is most likely to actually probe. */
const TOP_PRIORITY = 4;

export default function DeckDetailPage() {
  const { deckId } = useParams<{ deckId: string }>();
  const router = useRouter();
  const { session, sessionLoading } = useSession();
  const [detail, setDetail] = useState<DeckDetailResponse | null>(null);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [error, setError] = useState("");
  // One deck, so the parent conversation is unambiguous and stable for the
  // whole session — the case the shared side-chat hook is shaped for.
  const sideChats = useCardSideChats(deckId ?? null);

  const load = useCallback(async () => {
    try {
      const [payload, today] = await Promise.all([
        apiFetch<DeckDetailResponse>(`/decks/${deckId}`),
        apiFetch<ReviewQueue>(`/decks/queue?deck_id=${deckId}`),
      ]);
      setDetail(payload);
      setQueue(today);
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load that deck.");
    }
  }, [deckId]);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const cards = useMemo(() => {
    const all = detail?.cards ?? [];
    return filter === "top"
      ? all
          .filter((item) => item.card.interview_priority >= TOP_PRIORITY)
          .sort(
            (left, right) =>
              right.card.interview_priority - left.card.interview_priority,
          )
      : all;
  }, [detail, filter]);

  const reset = useCallback(async () => {
    try {
      await apiFetch(`/decks/${deckId}/reset`, { method: "POST" });
      await load();
    } catch (caught) {
      setError((caught as Error).message || "Could not reset that deck.");
    }
  }, [deckId, load]);

  if (sessionLoading) {
    return (
      <div className="grid h-dvh place-items-center p-6">
        <Skeleton className="h-6 w-48" />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="relative grid h-dvh place-items-center p-6">
        <div className="absolute right-3 top-3">
          <ThemeToggle />
        </div>
        <AuthGate />
      </div>
    );
  }

  const deck = detail?.deck;
  const dueNow = queue?.cards.length ?? 0;

  return (
    <AppShell
      nav={<SectionNav active="decks" />}
      overlay={
        <SideChatLayer
          windows={sideChats.windows}
          onRectChange={sideChats.setRect}
          onMinimize={sideChats.setMinimized}
          onClose={sideChats.close}
          onFocus={sideChats.focus}
          onSettled={sideChats.noteSettled}
          surface={BOOK_SIDE_CHATS}
          renderTurns={({ turns, isLoading, isQueued }) => (
            <SideChatTurns
              turns={turns as ChatTurn[]}
              isLoading={isLoading}
              isQueued={isQueued}
            />
          )}
          onAnchorsChange={(sideChatId, anchors) => {
            void sideChats.setAnchors(sideChatId, anchors);
          }}
          resolveQuoteTurn={() => null}
          error={sideChats.error}
          onDismissError={sideChats.dismissError}
        />
      }
      status={<span className="truncate">{deck?.title ?? "Deck"}</span>}
      account={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="max-w-44">
              <span className="truncate">{session.user.email}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="font-normal text-muted-foreground">
              Signed in
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => signOut()}>
              <LogOut aria-hidden />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      }
      rail={
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
          <Button variant="ghost" size="sm" className="justify-start" asChild>
            <Link href="/decks">
              <ArrowLeft aria-hidden />
              All decks
            </Link>
          </Button>

          {deck ? (
            <div className="space-y-3 border-t border-border pt-4">
              <div>
                <h2 className="font-heading text-sm font-medium">
                  How this deck was made
                </h2>
                {deck.generation_mode === "book_extracted" &&
                deck.metrics.source_questions_total === 0 &&
                !deck.metrics.notice ? (
                  <p className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs text-amber-800 dark:text-amber-300">
                    This deck predates source-question coverage checks.
                    Regenerate it before studying.
                  </p>
                ) : null}
                <dl className="mt-2 space-y-1.5 text-xs text-muted-foreground">
                  <div className="flex justify-between gap-2">
                    <dt>
                      {deck.generation_mode === "book_extracted"
                        ? "Questions found"
                        : "Topics required"}
                    </dt>
                    <dd className="tabular-nums">
                      {deck.generation_mode === "book_extracted"
                        ? deck.metrics.source_questions_total
                        : deck.metrics.topics_required}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt>
                      {deck.generation_mode === "book_extracted"
                        ? "Questions answered"
                        : "Topics covered"}
                    </dt>
                    <dd className="tabular-nums">
                      {deck.generation_mode === "book_extracted"
                        ? deck.metrics.source_questions_covered
                        : deck.metrics.topics_covered}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt>Cards written</dt>
                    <dd className="tabular-nums">
                      {deck.metrics.cards_generated}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    {/*
                      Dropped cards are shown rather than hidden. A deck that
                      quietly discarded a third of what it wrote is telling you
                      something about the chapter, or about the prompt.
                    */}
                    <dt>Dropped as ungrounded</dt>
                    <dd className="tabular-nums">
                      {deck.metrics.cards_dropped_uncited +
                        deck.metrics.cards_dropped_out_of_scope}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt>Dropped as duplicate</dt>
                    <dd className="tabular-nums">
                      {deck.metrics.cards_dropped_duplicate}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt>Repair pass</dt>
                    <dd>{deck.metrics.repair_attempted ? "yes" : "no"}</dd>
                  </div>
                </dl>
              </div>

              {(deck.generation_mode === "book_extracted"
                ? deck.metrics.uncovered_question_labels
                : deck.metrics.uncovered_topic_labels
              ).length > 0 ? (
                <div className="rounded-md border border-border p-2.5">
                  <p className="text-xs font-medium">Not covered</p>
                  <ul className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                    {(deck.generation_mode === "book_extracted"
                      ? deck.metrics.uncovered_question_labels
                      : deck.metrics.uncovered_topic_labels
                    ).map((label) => (
                      <li key={label} className="truncate" title={label}>
                        {label}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={() => void reset()}
              >
                Reset progress
              </Button>
            </div>
          ) : null}
        </div>
      }
    >
      <div className="mx-auto w-full max-w-3xl space-y-5 overflow-y-auto p-4 sm:p-6">
        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {reviewing && queue ? (
          <ReviewSession
            cards={queue.cards}
            onFinished={() => void load()}
            onExit={() => setReviewing(false)}
            onAskSelection={(card, quotedText) => {
              void sideChats.askAboutSelection(card, quotedText);
            }}
          />
        ) : !deck ? (
          <div className="space-y-2" aria-hidden>
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <>
            <div className="space-y-2">
              <h1 className="font-heading text-lg font-medium">{deck.title}</h1>
              <p className="text-sm text-muted-foreground">
                {deck.source_title}
              </p>
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="outline" className="font-normal tabular-nums">
                  {deck.card_count} cards
                </Badge>
                <CoverageBadge metrics={deck.metrics} />
                {deck.status === "partial" ? (
                  <Badge variant="outline" className="font-normal">
                    partial
                  </Badge>
                ) : null}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <Button
                disabled={dueNow === 0}
                onClick={() => setReviewing(true)}
              >
                {dueNow > 0
                  ? `Review ${dueNow} card${dueNow === 1 ? "" : "s"}`
                  : "Nothing due"}
              </Button>
              <div className="flex gap-1 rounded-lg bg-muted p-1">
                {(["all", "top"] as Filter[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setFilter(value)}
                    aria-pressed={filter === value}
                    className={
                      filter === value
                        ? "rounded-md bg-background px-3 py-1 text-sm font-medium shadow-sm"
                        : "rounded-md px-3 py-1 text-sm text-muted-foreground"
                    }
                  >
                    {value === "all" ? "All cards" : "Top questions"}
                  </button>
                ))}
              </div>
            </div>

            <ul className="space-y-2">
              {cards.map((item) => (
                <BrowsableCard key={item.card.card_id} item={item} />
              ))}
            </ul>
          </>
        )}
      </div>
    </AppShell>
  );
}

/** One card in the browse list: front visible, back on demand. */
function BrowsableCard({ item }: { item: QueueCard }) {
  const [open, setOpen] = useState(false);

  return (
    <li className="rounded-lg border border-border bg-card">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="w-full px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <p className="font-heading text-sm leading-snug">
            {plain(item.card.front)}
          </p>
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <CardMeta card={item.card} />
            <span className="text-xs text-muted-foreground">
              {item.review.state === "new"
                ? "new"
                : `next in ${describeInterval(item.review.interval_days)}`}
            </span>
          </div>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-3 border-t border-border px-4 py-4">
          <CardBackFace item={item} />
          <CardSources item={item} />
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}
