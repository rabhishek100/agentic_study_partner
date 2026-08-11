"use client";

import { Layers, LogOut } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { DeckJobRow, DeckRow } from "@/components/decks/deck-row";
import { GenerateDeck } from "@/components/decks/generate-deck";
import { ReviewSession } from "@/components/decks/review-session";
import { SectionNav } from "@/components/section-nav";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatTurns } from "@/components/side-chat/side-chat-turns";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useCardSideChats } from "@/hooks/use-card-side-chats";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn } from "@/lib/types";
import {
  type DeckJob,
  type DeckListResponse,
  type DeckPreferences,
  type DeckSummary,
  type ReviewQueue,
  jobIsLive,
} from "@/lib/deck-types";

/** How often a page with work in flight re-checks itself. */
const POLL_INTERVAL_MS = 4_000;

export default function DecksPage() {
  const { session, sessionLoading } = useSession();
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [jobs, setJobs] = useState<DeckJob[]>([]);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  // The deck of the card being asked about. Set on the first highlight so a
  // reader who never uses side chats never pays for the conversation lookup.
  const [askingDeckId, setAskingDeckId] = useState<string | null>(null);
  const sideChats = useCardSideChats(askingDeckId);

  const load = useCallback(async () => {
    try {
      const [library, today] = await Promise.all([
        apiFetch<DeckListResponse>("/decks"),
        apiFetch<ReviewQueue>("/decks/queue"),
      ]);
      setDecks(library.decks);
      setJobs(library.jobs);
      setQueue(today);
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load your decks.");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const working = useMemo(() => jobs.filter(jobIsLive), [jobs]);
  const recentlyFailed = useMemo(
    () => jobs.filter((job) => job.status === "failed").slice(0, 2),
    [jobs],
  );
  const generatedDecks = useMemo(
    () => decks.filter((deck) => deck.generation_mode !== "book_extracted"),
    [decks],
  );
  const bookQuestionDecks = useMemo(
    () => decks.filter((deck) => deck.generation_mode === "book_extracted"),
    [decks],
  );

  useEffect(() => {
    if (!session || working.length === 0 || reviewing) return;
    const timer = setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [session, working.length, reviewing, load]);

  const savePreferences = useCallback(
    async (preferences: DeckPreferences) => {
      try {
        await apiFetch<DeckPreferences>("/decks/preferences", {
          method: "PATCH",
          body: JSON.stringify(preferences),
        });
        await load();
      } catch (caught) {
        setError((caught as Error).message || "Could not save that setting.");
      }
    },
    [load],
  );

  const retryJob = useCallback(async (failed: DeckJob) => {
    try {
      const job = await apiFetch<DeckJob>("/decks", {
        method: "POST",
        body: JSON.stringify({
          source_kind: failed.source_kind,
          generation_mode: failed.generation_mode ?? "topic_generated",
          book_id: failed.book_id,
          node_id: failed.node_id,
          video_id: failed.video_id,
        }),
      });
      setJobs((current) => [
        job,
        ...current.filter((item) => item.job_id !== job.job_id),
      ]);
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not retry that deck.");
    }
  }, []);

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

  const dueToday = queue?.cards.length ?? 0;

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
          // Every anchor on a card points at the card's own turn, which the
          // server assigned; there is no second turn here to disambiguate.
          resolveQuoteTurn={() => null}
          error={sideChats.error}
          onDismissError={sideChats.dismissError}
        />
      }
      status={
        <span>
          {decks.length} deck{decks.length === 1 ? "" : "s"}
          {dueToday > 0 ? ` · ${dueToday} to review` : " · nothing due"}
        </span>
      }
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
        <div className="flex h-full flex-col gap-5 overflow-y-auto p-4">
          <div className="sm:hidden">
            <SectionNav active="decks" />
          </div>
          <div>
            <h2 className="mb-1 font-heading text-sm font-medium">
              Make a deck
            </h2>
            <p className="mb-3 text-xs text-muted-foreground">
              Pick a chapter or a lecture. Every topic in it gets at least one
              card, ranked by how likely an interviewer is to ask.
            </p>
            <GenerateDeck
              onQueued={(job) => {
                setJobs((current) => [job, ...current]);
              }}
            />
          </div>

          {queue ? (
            <div className="space-y-3 border-t border-border pt-4">
              <h2 className="font-heading text-sm font-medium">Daily pace</h2>
              <div className="space-y-1.5">
                <Label htmlFor="new-per-day" className="text-xs">
                  New cards per day
                </Label>
                <Input
                  id="new-per-day"
                  type="number"
                  min={0}
                  max={200}
                  defaultValue={queue.new_cards_per_day}
                  className="h-8"
                  onBlur={(event) => {
                    const value = Number(event.target.value);
                    if (value === queue.new_cards_per_day) return;
                    void savePreferences({
                      new_cards_per_day: value,
                      max_reviews_per_day: queue.max_reviews_per_day,
                    });
                  }}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="max-per-day" className="text-xs">
                  Review ceiling per day
                </Label>
                <Input
                  id="max-per-day"
                  type="number"
                  min={1}
                  max={1000}
                  defaultValue={queue.max_reviews_per_day}
                  className="h-8"
                  onBlur={(event) => {
                    const value = Number(event.target.value);
                    if (value === queue.max_reviews_per_day) return;
                    void savePreferences({
                      new_cards_per_day: queue.new_cards_per_day,
                      max_reviews_per_day: value,
                    });
                  }}
                />
              </div>
              <p className="text-xs text-muted-foreground">
                {queue.reviewed_today} reviewed today.
              </p>
            </div>
          ) : null}
        </div>
      }
    >
      <div className="mx-auto w-full max-w-3xl space-y-6 overflow-y-auto p-4 sm:p-6">
        {reviewing && queue ? (
          <ReviewSession
            cards={queue.cards}
            onFinished={() => void load()}
            onExit={() => setReviewing(false)}
            onAskSelection={(card, quotedText) => {
              setAskingDeckId(card.deck_id);
              void sideChats.askAboutSelection(card, quotedText);
            }}
          />
        ) : (
          <>
            <div>
              <h1 className="font-heading text-lg font-medium">Cards</h1>
              <p className="text-sm text-muted-foreground">
                A few minutes a day. Cards you struggle with come back sooner;
                cards you know get out of the way.
              </p>
            </div>

            {error ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}

            <section
              aria-labelledby="today"
              className="rounded-xl border border-border bg-card p-5"
            >
              <h2 id="today" className="font-heading text-sm font-medium">
                Today
              </h2>
              {queue && dueToday > 0 ? (
                <>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {queue.due_total} due
                    {queue.cards.length - queue.due_total > 0
                      ? ` · ${queue.cards.length - queue.due_total} new`
                      : ""}
                    , across every deck.
                  </p>
                  <Button className="mt-4" onClick={() => setReviewing(true)}>
                    Review {dueToday} card{dueToday === 1 ? "" : "s"}
                  </Button>
                </>
              ) : (
                <p className="mt-1 text-sm text-muted-foreground">
                  {decks.length === 0
                    ? "Make a deck to start."
                    : "Nothing due right now. Come back tomorrow, or raise the daily new-card allowance."}
                </p>
              )}
            </section>

            {recentlyFailed.length > 0 || working.length > 0 ? (
              <ul className="space-y-2">
                {working.map((job) => (
                  <DeckJobRow key={job.job_id} job={job} />
                ))}
                {recentlyFailed.map((job) => (
                  <DeckJobRow
                    key={job.job_id}
                    job={job}
                    onRetry={(failed) => void retryJob(failed)}
                  />
                ))}
              </ul>
            ) : null}

            <section aria-labelledby="generated-decks">
              <h2
                id="generated-decks"
                className="mb-1 font-heading text-sm font-medium"
              >
                Generated revision cards
              </h2>
              <p className="mb-2 text-xs text-muted-foreground">
                Questions written from chapter topics or lecture evidence.
              </p>
              {!loaded ? (
                <div className="space-y-2" aria-hidden>
                  <Skeleton className="h-24 w-full" />
                  <Skeleton className="h-24 w-full" />
                </div>
              ) : decks.length === 0 ? (
                <div className="rounded-lg border border-dashed border-border p-8 text-center">
                  <Layers aria-hidden className="mx-auto mb-2 size-6 opacity-60" />
                  <p className="text-sm font-medium">No decks yet</p>
                  <p className="text-sm text-muted-foreground">
                    Generate one from a chapter or a lecture in the panel.
                  </p>
                </div>
              ) : generatedDecks.length > 0 ? (
                <ul className="space-y-2">
                  {generatedDecks.map((deck) => (
                    <DeckRow key={deck.deck_id} deck={deck} />
                  ))}
                </ul>
              ) : (
                <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
                  No AI-generated revision decks yet.
                </p>
              )}
            </section>

            <section aria-labelledby="book-question-decks">
              <h2
                id="book-question-decks"
                className="mb-1 font-heading text-sm font-medium"
              >
                Questions from books
              </h2>
              <p className="mb-2 text-xs text-muted-foreground">
                Exercises and review questions printed in the source PDF, kept
                separate from AI-generated questions.
              </p>
              {!loaded ? (
                <Skeleton className="h-24 w-full" aria-hidden />
              ) : bookQuestionDecks.length > 0 ? (
                <ul className="space-y-2">
                  {bookQuestionDecks.map((deck) => (
                    <DeckRow key={deck.deck_id} deck={deck} />
                  ))}
                </ul>
              ) : (
                <p className="rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
                  No book-question decks yet. Choose “Use questions from book”
                  when making a chapter deck.
                </p>
              )}
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}
