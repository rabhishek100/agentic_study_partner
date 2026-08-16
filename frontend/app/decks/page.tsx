"use client";

import { AlertCircle, BookOpen, LogOut, Search, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { CardsSettings } from "@/components/decks/cards-settings";
import { DeckJobRow, DeckRow } from "@/components/decks/deck-row";
import { GenerateDeck } from "@/components/decks/generate-deck";
import { ReviewSession } from "@/components/decks/review-session";
import { SectionNav } from "@/components/section-nav";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatTurns } from "@/components/side-chat/side-chat-turns";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useCardSideChats } from "@/hooks/use-card-side-chats";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import {
  type DeckJob,
  type DeckListResponse,
  type DeckPreferences,
  type DeckSummary,
  type ReviewQueue,
  coveragePercent,
  jobIsLive,
} from "@/lib/deck-types";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn } from "@/lib/types";

const POLL_INTERVAL_MS = 4_000;
type DeckFilter = "all" | "topic_generated" | "book_extracted";

function DeckGroup({
  id,
  title,
  count,
  mode,
  decks,
}: {
  id: string;
  title: string;
  count: number;
  mode: "topic_generated" | "book_extracted";
  decks: DeckSummary[];
}) {
  const Icon = mode === "book_extracted" ? BookOpen : Sparkles;
  return (
    <section aria-labelledby={id}>
      <div className="mb-2 flex items-center gap-2 px-1">
        <Icon aria-hidden className="size-4 text-primary" />
        <h3 id={id} className="text-base font-medium">
          {title}
        </h3>
        <Badge variant="secondary" className="font-normal tabular-nums">
          {count} deck{count === 1 ? "" : "s"}
        </Badge>
      </div>
      <div className="overflow-hidden rounded-lg border border-border">
        <div
          aria-hidden
          className="hidden grid-cols-[minmax(0,2.6fr)_minmax(6rem,1.1fr)_3.5rem_5rem_7.75rem_5.5rem_4.5rem] gap-3 border-b border-border bg-surface px-4 py-2 text-xs font-medium text-muted-foreground sm:grid"
        >
          <span>Deck</span>
          <span>Source</span>
          <span>Cards</span>
          <span>Coverage</span>
          <span>Due / New</span>
          <span>Updated</span>
          <span>Action</span>
        </div>
        <ul>
          {decks.map((deck) => (
            <DeckRow key={deck.deck_id} deck={deck} />
          ))}
        </ul>
      </div>
    </section>
  );
}

export default function DecksPage() {
  const { session, sessionLoading } = useSession();
  const [decks, setDecks] = useState<DeckSummary[]>([]);
  const [jobs, setJobs] = useState<DeckJob[]>([]);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<DeckFilter>("all");
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

  useEffect(() => {
    if (!session || working.length === 0 || reviewing) return;
    const timer = window.setInterval(() => void load(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
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
        ...current.filter((item) => item.job_id !== failed.job_id),
      ]);
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not retry that deck.");
    }
  }, []);

  const cancelJob = useCallback(async (job: DeckJob) => {
    try {
      await apiFetch<void>(`/decks/jobs/${job.job_id}/cancel`, { method: "POST" });
      setJobs((current) =>
        current.map((item) =>
          item.job_id === job.job_id
            ? { ...item, status: "cancelled", stage: "pending" }
            : item,
        ),
      );
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not cancel generation.");
    }
  }, []);

  const filteredDecks = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return decks.filter((deck) => {
      if (filter !== "all" && deck.generation_mode !== filter) return false;
      if (!normalized) return true;
      return `${deck.title} ${deck.source_title}`
        .toLocaleLowerCase()
        .includes(normalized);
    });
  }, [decks, filter, query]);
  const generatedDecks = useMemo(
    () => filteredDecks.filter((deck) => deck.generation_mode !== "book_extracted"),
    [filteredDecks],
  );
  const bookQuestionDecks = useMemo(
    () => filteredDecks.filter((deck) => deck.generation_mode === "book_extracted"),
    [filteredDecks],
  );

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
  const scheduledNew = queue
    ? Math.max(0, queue.cards.length - Math.min(queue.cards.length, queue.due_total))
    : 0;
  const coverage = decks.length
    ? Math.round(
        decks.reduce(
          (sum, deck) => sum + coveragePercent(deck.metrics) * Math.max(1, deck.card_count),
          0,
        ) / decks.reduce((sum, deck) => sum + Math.max(1, deck.card_count), 0),
      )
    : 0;
  const preferences = queue
    ? {
        new_cards_per_day: queue.new_cards_per_day,
        max_reviews_per_day: queue.max_reviews_per_day,
      }
    : null;

  return (
    <AppShell
      nav={<SectionNav active="decks" />}
      railMode="drawer-only"
      rail={
        <div className="space-y-5 p-4">
          <SectionNav active="decks" />
          <p className="border-t border-border pt-4 text-xs leading-5 text-muted-foreground">
            Create decks and adjust your daily pace from the Cards workspace.
          </p>
        </div>
      }
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
    >
      <div className="h-full overflow-y-auto">
        {reviewing && queue ? (
          <div className="mx-auto w-full max-w-5xl p-4 sm:p-6">
            <ReviewSession
              cards={queue.cards}
              onFinished={() => void load()}
              onExit={() => setReviewing(false)}
              onAskSelection={(card, quotedText) => {
                setAskingDeckId(card.deck_id);
                void sideChats.askAboutSelection(card, quotedText);
              }}
            />
          </div>
        ) : (
          <div className="mx-auto w-full max-w-[100rem] space-y-5 p-4 sm:p-6 lg:p-8">
            <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
              <div>
                <h1 className="font-serif text-3xl font-medium tracking-tight">Cards</h1>
                <p className="mt-1 text-sm text-muted-foreground">
                  Your review queue, decks, and generation activity at a glance.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <GenerateDeck
                  onQueued={(job) => setJobs((current) => [job, ...current])}
                />
                <CardsSettings
                  preferences={preferences}
                  reviewedToday={queue?.reviewed_today ?? 0}
                  onSave={savePreferences}
                />
              </div>
            </header>

            {error ? (
              <Alert variant="destructive">
                <AlertCircle aria-hidden />
                <AlertTitle>Cards could not be updated</AlertTitle>
                <AlertDescription>
                  <p>Check your connection and try again. Your saved decks were not changed.</p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <Button variant="outline" size="sm" onClick={() => void load()}>
                      Try again
                    </Button>
                    <details>
                      <summary className="cursor-pointer text-xs text-muted-foreground">
                        Technical details
                      </summary>
                      <p className="mt-1 break-words font-mono text-xs text-muted-foreground">
                        {error}
                      </p>
                    </details>
                  </div>
                </AlertDescription>
              </Alert>
            ) : null}

            <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.85fr)_minmax(24rem,1fr)]">
              <div className="min-w-0 space-y-5">
                <section
                  aria-labelledby="today-heading"
                  className="grid gap-5 rounded-lg border border-border bg-surface p-4 sm:grid-cols-[minmax(13.5rem,1.9fr)_repeat(4,minmax(4.5rem,1fr))] sm:items-center"
                >
              <div className="sm:border-r sm:border-border sm:pr-5">
                <h2 id="today-heading" className="text-base font-medium">
                  Today
                </h2>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <p className="text-xs text-muted-foreground">
                    {queue
                      ? `${queue.due_total} due · ${scheduledNew} new · across all decks`
                      : "Loading your review queue…"}
                  </p>
                  {dueToday > 0 ? (
                    <Button size="xs" onClick={() => setReviewing(true)}>
                      Review {dueToday}
                    </Button>
                  ) : null}
                </div>
              </div>
              <div>
                <p className="text-xl font-medium tabular-nums">{dueToday}</p>
                <p className="text-xs text-muted-foreground">In today’s review</p>
              </div>
              <div>
                <p className="text-xl font-medium tabular-nums">{scheduledNew}</p>
                <p className="text-xs text-muted-foreground">New cards today</p>
              </div>
              <div>
                <p className="text-xl font-medium tabular-nums">
                  {queue?.max_reviews_per_day ?? "—"}
                </p>
                <p className="text-xs text-muted-foreground">Daily review ceiling</p>
              </div>
              <div>
                <p className="text-xl font-medium tabular-nums">{coverage}%</p>
                <p className="text-xs text-muted-foreground">Coverage across decks</p>
              </div>
                </section>

                <section aria-labelledby="deck-library-heading" className="min-w-0">
                <div className="mb-5 flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
                  <div>
                    <h2 id="deck-library-heading" className="text-lg font-medium">
                      Deck library
                    </h2>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      AI-written revision cards and printed book questions stay clearly separated.
                    </p>
                  </div>
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <div className="relative">
                      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                        placeholder="Search decks"
                        aria-label="Search decks"
                        className="w-full pl-9 sm:w-56"
                      />
                    </div>
                    <Select value={filter} onValueChange={(value) => setFilter(value as DeckFilter)}>
                      <SelectTrigger className="w-full sm:w-40" aria-label="Filter decks">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All decks</SelectItem>
                        <SelectItem value="topic_generated">AI-generated</SelectItem>
                        <SelectItem value="book_extracted">From books</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {!loaded ? (
                  <div className="space-y-4" aria-hidden>
                    <Skeleton className="h-36 w-full" />
                    <Skeleton className="h-28 w-full" />
                  </div>
                ) : filteredDecks.length === 0 ? (
                  <div className="rounded-lg border border-dashed border-border p-8 text-center">
                    <BookOpen aria-hidden className="mx-auto mb-2 size-6 text-muted-foreground" />
                    <p className="text-sm font-medium">
                      {decks.length === 0 ? "No decks yet" : "No matching decks"}
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {decks.length === 0
                        ? "Create a deck from a chapter or lecture to get started."
                        : "Try another search or filter."}
                    </p>
                  </div>
                ) : (
                  <div className="space-y-7">
                    {generatedDecks.length > 0 ? (
                      <DeckGroup
                        id="ai-generated-decks"
                        title="AI-generated"
                        count={generatedDecks.length}
                        mode="topic_generated"
                        decks={generatedDecks}
                      />
                    ) : null}
                    {bookQuestionDecks.length > 0 ? (
                      <DeckGroup
                        id="book-question-decks"
                        title="From books"
                        count={bookQuestionDecks.length}
                        mode="book_extracted"
                        decks={bookQuestionDecks}
                      />
                    ) : null}
                    <p className="px-1 text-xs text-muted-foreground">
                      Showing {filteredDecks.length} of {decks.length} deck{decks.length === 1 ? "" : "s"}
                    </p>
                  </div>
                )}
                </section>
              </div>

              <aside
                aria-labelledby="generation-activity-heading"
                className="rounded-lg border border-border bg-surface p-4 xl:sticky xl:top-6"
              >
                <div className="mb-4">
                  <h2 id="generation-activity-heading" className="text-lg font-medium">
                    Generation activity
                  </h2>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Track book extraction and AI generation without keeping this page open.
                  </p>
                </div>
                {working.length > 0 || recentlyFailed.length > 0 ? (
                  <ul className="space-y-3">
                    {working.map((job) => (
                      <DeckJobRow
                        key={job.job_id}
                        job={job}
                        onCancel={(active) => void cancelJob(active)}
                      />
                    ))}
                    {recentlyFailed.map((job) => (
                      <DeckJobRow
                        key={job.job_id}
                        job={job}
                        onRetry={(failed) => void retryJob(failed)}
                      />
                    ))}
                  </ul>
                ) : (
                  <div className="rounded-lg border border-dashed border-border px-5 py-8 text-center">
                    <Sparkles aria-hidden className="mx-auto mb-2 size-5 text-primary" />
                    <p className="text-sm font-medium">No generation in progress</p>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      Start a deck and its percentage, current step, and time estimate will appear here.
                    </p>
                  </div>
                )}
              </aside>
            </div>
          </div>
        )}
      </div>
    </AppShell>
  );
}
