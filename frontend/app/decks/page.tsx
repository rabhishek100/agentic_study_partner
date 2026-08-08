"use client";

import { Layers, LogOut } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { DeckJobRow, DeckRow } from "@/components/decks/deck-row";
import { GenerateDeck } from "@/components/decks/generate-deck";
import { ReviewSession } from "@/components/decks/review-session";
import { SectionNav } from "@/components/section-nav";
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
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
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
                  <DeckJobRow key={job.job_id} job={job} />
                ))}
              </ul>
            ) : null}

            <section aria-labelledby="decks">
              <h2 id="decks" className="mb-2 font-heading text-sm font-medium">
                Your decks
              </h2>
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
              ) : (
                <ul className="space-y-2">
                  {decks.map((deck) => (
                    <DeckRow key={deck.deck_id} deck={deck} />
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </AppShell>
  );
}
