"use client";

import { LogOut, Video } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { AddVideo } from "@/components/video/add-video";
import {
  IngestionStatus,
  readinessLabel,
} from "@/components/video/ingestion-status";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { Skeleton } from "@/components/ui/skeleton";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import {
  formatTimestamp,
  type VideoListResponse,
  type VideoSummary,
} from "@/lib/video-types";

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

  const processing = videos.some(
    (video) => video.readiness_status === "processing",
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
          {videos.length} {videos.length === 1 ? "video" : "videos"}
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
            <h2 className="font-heading text-sm font-medium">Add a lecture</h2>
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
          <h1 className="font-heading text-lg font-medium">Videos</h1>
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
          <ul className="space-y-3">
            {videos.map((video) => (
              <li key={video.video_id}>
                <Link
                  href={`/videos/${video.video_id}`}
                  className="block rounded-lg border border-border p-4 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 truncate font-medium">
                      {video.title}
                    </span>
                    <Badge
                      variant={
                        video.readiness_status === "failed"
                          ? "destructive"
                          : "outline"
                      }
                    >
                      {readinessLabel(video.readiness_status, video.latest_ingestion)}
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {video.source_kind === "youtube" ? "YouTube" : "Uploaded"}
                    {video.duration_ms
                      ? ` · ${formatTimestamp(video.duration_ms)}`
                      : null}
                  </p>
                  {video.readiness_status === "processing" ||
                  video.latest_ingestion?.error ||
                  video.latest_ingestion?.status === "awaiting_upload" ? (
                    <div className="mt-3">
                      <IngestionStatus
                        readiness={video.readiness_status}
                        ingestion={video.latest_ingestion}
                      />
                    </div>
                  ) : null}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
