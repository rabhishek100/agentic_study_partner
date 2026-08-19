"use client";

import { LogOut, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { AddVideoDialog } from "@/components/video/add-video-dialog";
import { ContinueBand } from "@/components/video/continue-band";
import {
  ContinueSessions,
  sessionDetail,
} from "@/components/read/continue-sessions";
import { FirstRun } from "@/components/video/first-run";
import { ProcessingBand } from "@/components/video/processing-band";
import { VideoCard } from "@/components/video/video-card";
import { VideoTile } from "@/components/video/video-tile";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { signOut, useSession } from "@/hooks/use-session";
import { useWatchSessions } from "@/hooks/use-source-sessions";
import { timecode } from "@/lib/timecode";
import { apiFetch } from "@/lib/api";
import {
  latestByVideo,
  matchesQuery,
  SEARCH_THRESHOLD,
  sortByActivity,
} from "@/lib/video-activity";
import { isUsable, videoState } from "@/lib/video-state";
import type {
  VideoConversationSummary,
  VideoListResponse,
  VideoSummary,
} from "@/lib/video-types";
import { cn } from "@/lib/utils";

/** How often a processing library re-checks itself. */
const POLL_INTERVAL_MS = 5_000;

/**
 * What the reader can narrow the library to.
 *
 * The same three groups the page has always used, from `videoState` — they
 * are a filter now rather than three stacked headings, so the counts answer
 * "is anything wrong?" without scrolling to find out.
 */
type Filter = "all" | "library" | "processing" | "attention";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All lectures" },
  { key: "library", label: "Ready to ask" },
  { key: "processing", label: "Processing" },
  { key: "attention", label: "Needs attention" },
];

export default function VideosPage() {
  const { session, sessionLoading } = useSession();
  const { sessions: watchSessions } = useWatchSessions(Boolean(session));
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [conversations, setConversations] = useState<VideoConversationSummary[]>(
    [],
  );
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const payload = await apiFetch<VideoListResponse>("/videos");
      setVideos(payload.videos);
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load your videos.");
    } finally {
      setLoaded(true);
    }
  }, []);

  /*
    Conversations load beside the library rather than with it. They decide an
    ordering and a line of text; a library that cannot be listed is a broken
    page, but a Continue band that cannot be listed is one absent shortcut, so
    a failure here stays silent rather than raising an alarm over the lectures.
  */
  const loadActivity = useCallback(async () => {
    try {
      const payload = await apiFetch<{
        conversations: VideoConversationSummary[];
      }>("/video-conversations");
      setConversations(payload.conversations);
    } catch {
      setConversations([]);
    }
  }, []);

  useEffect(() => {
    if (!session) return;
    load();
    loadActivity();
  }, [session, load, loadActivity]);

  const retry = useCallback(
    async (video: VideoSummary) => {
      setError("");
      try {
        await apiFetch(`/videos/${video.video_id}/reingest`, {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
        });
        await load();
      } catch (caught) {
        setError((caught as Error).message || "Could not start processing again.");
      }
    },
    [load],
  );

  const remove = useCallback(
    async (video: VideoSummary) => {
      setError("");
      try {
        await apiFetch(`/videos/${video.video_id}`, { method: "DELETE" });
        await load();
      } catch (caught) {
        setError((caught as Error).message || "Could not remove that lecture.");
      }
    },
    [load],
  );

  const latest = useMemo(() => latestByVideo(conversations), [conversations]);

  const { library, processing, attention } = useMemo(() => {
    const buckets = {
      library: [] as VideoSummary[],
      processing: [] as VideoSummary[],
      attention: [] as VideoSummary[],
    };
    for (const video of sortByActivity(videos, latest)) {
      const state = videoState(video);
      if (isUsable(state)) buckets.library.push(video);
      else if (state === "processing") buckets.processing.push(video);
      else buckets.attention.push(video);
    }
    return buckets;
  }, [videos, latest]);

  const searchable = videos.length >= SEARCH_THRESHOLD;
  const matching = (group: VideoSummary[]) =>
    searchable ? group.filter((video) => matchesQuery(video, query)) : group;
  const shown = {
    library: matching(library),
    processing: matching(processing),
    attention: matching(attention),
  };
  const nothingMatches =
    searchable && query.trim().length > 0 &&
    shown.library.length + shown.processing.length + shown.attention.length === 0;

  const counts: Record<Filter, number> = {
    all: videos.length,
    library: library.length,
    processing: processing.length,
    attention: attention.length,
  };

  const shows = (key: Exclude<Filter, "all">) => filter === "all" || filter === key;

  useEffect(() => {
    if (!session || processing.length === 0) return;
    const timer = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [session, processing.length, load]);

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

  return (
    <AppShell
      nav={<SectionNav active="videos" />}
      status={
        <span>
          {/* What the page is for: lectures that can answer, not rows. */}
          {library.length} ready
          {processing.length > 0 ? ` · ${processing.length} processing` : ""}
          {attention.length > 0 ? ` · ${attention.length} need attention` : ""}
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
        <div className="flex h-full flex-col gap-6 overflow-y-auto p-4">
          <div className="sm:hidden">
            <SectionNav active="videos" />
          </div>

          <AddVideoDialog onAdded={load} />

          {/* Continuing a *lecture* comes before continuing a thread about
              one: what a viewer wants back is the moment they stopped at and
              the questions they left along it. */}
          <ContinueSessions
            heading="Continue watching"
            entries={watchSessions.map((watch) => ({
              key: watch.conversation_id,
              href: `/watch/${watch.video_id}`,
              title: watch.title,
              detail: sessionDetail(
                watch.position ? timecode(watch.position.timestamp_ms) : null,
                watch.question_count,
              ),
            }))}
          />
          <ContinueBand conversations={conversations} />

          {videos.length > 0 ? (
            <nav aria-label="Filter the library" className="flex flex-col gap-1">
              <p className="px-2 text-eyebrow font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                Library
              </p>
              {FILTERS.map(({ key, label }) => {
                // A filter for a group with nothing in it is a control that
                // can only ever empty the screen.
                if (key !== "all" && counts[key] === 0) return null;
                const active = filter === key;
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setFilter(key)}
                    className={cn(
                      "flex items-center justify-between rounded-md px-2 py-2 text-sm transition-colors",
                      active
                        ? "bg-wash font-medium text-foreground"
                        : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
                    )}
                  >
                    <span>{label}</span>
                    <span className="font-mono text-xs tabular-nums">
                      {counts[key]}
                    </span>
                  </button>
                );
              })}
            </nav>
          ) : null}
        </div>
      }
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[80rem] flex-col gap-6 px-4 py-6 sm:px-8">
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h1 className="font-serif text-xl font-semibold tracking-tight">
              Videos
            </h1>
            <p className="text-xs text-muted-foreground">
              Ask a lecture anything — grounded in the transcript, what was on
              screen, and any linked slides.
            </p>
          </div>

          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          {searchable ? (
            <div className="relative max-w-sm">
              <Search
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              />
              <Input
                type="search"
                aria-label="Search lectures by title"
                placeholder="Search lectures…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                className="pl-9"
              />
            </div>
          ) : null}

          {!loaded ? (
            <div
              className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3"
              aria-hidden
            >
              <Skeleton className="aspect-video w-full rounded-lg" />
              <Skeleton className="aspect-video w-full rounded-lg" />
              <Skeleton className="aspect-video w-full rounded-lg" />
            </div>
          ) : videos.length === 0 ? (
            <FirstRun onAdded={load} />
          ) : (
            <>
              {shows("processing") ? (
                <ProcessingBand videos={shown.processing} />
              ) : null}

              {shows("library") && shown.library.length > 0 ? (
                <section aria-labelledby="library-heading" className="flex flex-col gap-3">
                  <h2 id="library-heading" className="sr-only">
                    Ready to ask
                  </h2>
                  <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                    {shown.library.map((video) => (
                      <VideoTile
                        key={video.video_id}
                        video={video}
                        onDelete={remove}
                        lastAsked={latest.get(video.video_id)?.updated_at ?? null}
                      />
                    ))}
                  </ul>
                </section>
              ) : null}

              {shows("attention") && shown.attention.length > 0 ? (
                <section aria-labelledby="attention-heading" className="flex flex-col gap-2">
                  <div>
                    <h2 id="attention-heading" className="text-sm font-medium">
                      Needs attention
                    </h2>
                    <p className="text-xs text-muted-foreground">
                      These are not ready and will not become ready on their own.
                    </p>
                  </div>
                  <ul className="flex flex-col gap-2">
                    {shown.attention.map((video) => (
                      <VideoCard
                        key={video.video_id}
                        video={video}
                        onRetry={retry}
                        onDelete={remove}
                      />
                    ))}
                  </ul>
                </section>
              ) : null}

              {/* A filter that hides everything says so, rather than showing
                  the reader an empty canvas they have to diagnose. */}
              {nothingMatches ? (
                <p className="text-sm text-muted-foreground">
                  No lecture matches “{query.trim()}”. Search reads titles.
                </p>
              ) : filter !== "all" && counts[filter] === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing here right now.
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </AppShell>
  );
}
