"use client";

import { LogOut, Video } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { AddVideo } from "@/components/video/add-video";
import { VideoCard } from "@/components/video/video-card";
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
import { Skeleton } from "@/components/ui/skeleton";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { groupVideos, videoState } from "@/lib/video-state";
import type { VideoListResponse, VideoSummary } from "@/lib/video-types";

/** How often a processing library re-checks itself. */
const POLL_INTERVAL_MS = 5_000;

export default function VideosPage() {
  const { session, sessionLoading } = useSession();
  const [videos, setVideos] = useState<VideoSummary[]>([]);
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

  useEffect(() => {
    if (session) load();
  }, [session, load]);

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

  const groups = groupVideos(videos);
  const readyCount = videos.filter((video) =>
    ["ready", "partial"].includes(videoState(video)),
  ).length;

  const processing = videos.some(
    (video) => videoState(video) === "processing",
  );
  useEffect(() => {
    if (!session || !processing) return;
    const timer = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [session, processing, load]);

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
          {readyCount} ready{videos.length > readyCount
            ? ` · ${videos.length - readyCount} not ready`
            : ""}
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
        <div className="flex h-full flex-col gap-4 overflow-y-auto p-4">
          <div className="sm:hidden">
            <SectionNav active="videos" />
          </div>
          <div>
            <h2 className="text-sm font-medium">Add a lecture</h2>
            <p className="mb-3 text-xs text-muted-foreground">
              Paste a YouTube link or upload a file. Slides are optional and can
              be attached now.
            </p>
            <AddVideo onAdded={load} />
          </div>
        </div>
      }
    >
      <div className="mx-auto w-full max-w-3xl space-y-4 overflow-y-auto p-4 sm:p-6">
        <div>
          <h1 className="font-serif text-lg font-medium">Videos</h1>
          <p className="text-sm text-muted-foreground">
            Lectures you can ask about — grounded in the transcript, what was on
            screen, and any linked slides.
          </p>
        </div>

        {error ? (
          <Alert variant="destructive">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {!loaded ? (
          <div className="space-y-2" aria-hidden>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : videos.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border p-8 text-center">
            <Video aria-hidden className="mx-auto mb-2 size-6 opacity-60" />
            <p className="text-sm font-medium">No videos yet</p>
            <p className="text-sm text-muted-foreground">
              Add a lecture from the panel to start asking questions about it.
            </p>
          </div>
        ) : (
          <div className="space-y-8">
            {groups.map((group) => (
              <section key={group.key} aria-labelledby={`group-${group.key}`}>
                <div className="mb-2">
                  <h2
                    id={`group-${group.key}`}
                    className="font-serif text-sm font-medium"
                  >
                    {group.title}
                    <span className="ml-2 text-xs font-normal text-muted-foreground tabular-nums">
                      {group.videos.length}
                    </span>
                  </h2>
                  <p className="text-xs text-muted-foreground">
                    {group.description}
                  </p>
                </div>
                <ul className="space-y-2">
                  {group.videos.map((video) => (
                    <VideoCard
                      key={video.video_id}
                      video={video}
                      onRetry={retry}
                      onDelete={remove}
                    />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
