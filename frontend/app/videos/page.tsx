"use client";

import { ChevronDown, Library, LogOut, Plus, Video } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { AddVideo } from "@/components/video/add-video";
import { VideoFeatureCard } from "@/components/video/video-feature-card";
import { VideoCard } from "@/components/video/video-card";
import { Alert, AlertDescription } from "@/components/ui/alert";
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
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { groupVideos, videoState } from "@/lib/video-state";
import type {
  VideoDetail,
  VideoListResponse,
  VideoSummary,
  VideoTimelineEntry,
} from "@/lib/video-types";

/** How often a processing library re-checks itself. */
const POLL_INTERVAL_MS = 5_000;

const DESIGN_PREVIEW_VIDEO: VideoSummary = {
  video_id: "design-preview-video",
  title: "cme295-lecture1-h264.mp4",
  description: "Machine learning lecture",
  source_kind: "upload",
  duration_ms: 6_118_000,
  readiness_status: "ready",
  ready_for_qa: true,
  playback: { kind: "local", youtube_video_id: null, media_url: null },
  latest_ingestion: null,
  readiness_notes: [],
  deletable: true,
  created_at: "2026-08-05T10:00:00Z",
  updated_at: "2026-08-05T10:00:00Z",
  ready_at: "2026-08-05T10:00:00Z",
};

const DESIGN_PREVIEW_DETAIL: VideoDetail = {
  ...DESIGN_PREVIEW_VIDEO,
  source: {
    source_kind: "upload",
    status: "ready",
    source_url: null,
    youtube_video_id: null,
    original_filename: "cme295-lecture1-h264.mp4",
  },
  chapters: [],
  resources: [],
  quality_gates: {},
};

const DESIGN_PREVIEW_TIMELINE: VideoTimelineEntry[] = [
  {
    frame_id: -1,
    timestamp_ms: 30_000,
    summary: "Policy Gradient Theorem lecture slide",
    visual_types: ["slide", "lecture"],
    ocr_text: "Policy Gradient Theorem",
    image_url: "/video/mugensei-lecture-preview.png",
  },
];

function VideoLibraryRail({
  readyCount,
  total,
  onAdd,
}: {
  readyCount: number;
  total: number;
  onAdd(): void;
}) {
  return (
    <div className="flex h-full flex-col p-4">
      <div className="flex items-center gap-2 text-sm">
        <span aria-hidden className="size-2.5 rounded-full bg-positive" />
        <span>{readyCount} ready</span>
      </div>
      <Button size="lg" className="mt-5 w-full" onClick={onAdd}>
        <Plus aria-hidden />
        Add lecture
      </Button>
      <div className="my-5 border-t border-sidebar-border" />
      <Link
        href="/videos"
        aria-current="page"
        className="flex items-center gap-2 rounded-lg bg-sidebar-accent px-3 py-3 text-sm font-medium text-sidebar-accent-foreground"
      >
        <Library aria-hidden className="size-4 text-primary" />
        All videos
        <span className="ml-auto font-mono text-xs tabular-nums">{total}</span>
      </Link>
      <div className="mt-auto border-t border-sidebar-border pt-5">
        <p className="font-heading text-sm font-medium">The endless path</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Every answer remains grounded in the lecture&apos;s transcript, screen,
          and linked slides.
        </p>
      </div>
    </div>
  );
}

export default function VideosPage() {
  const { session, sessionLoading } = useSession();
  const [videos, setVideos] = useState<VideoSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [addOpen, setAddOpen] = useState(false);
  const [featuredDetail, setFeaturedDetail] = useState<VideoDetail | null>(null);
  const [featuredTimeline, setFeaturedTimeline] = useState<VideoTimelineEntry[]>([]);
  const [featuredEvidenceLoaded, setFeaturedEvidenceLoaded] = useState(false);
  const [designPreview, setDesignPreview] = useState(false);

  useEffect(() => {
    setDesignPreview(
      process.env.NODE_ENV === "development" &&
        new URLSearchParams(window.location.search).get("design-preview") === "videos",
    );
  }, []);

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

  const visibleVideos = designPreview && videos.length === 0
    ? [DESIGN_PREVIEW_VIDEO]
    : videos;
  const groups = groupVideos(visibleVideos);
  const featuredVideo =
    groups.find((group) => group.key === "library")?.videos[0] ?? null;
  const remainingGroups = groups
    .map((group) => ({
      ...group,
      videos: group.videos.filter(
        (video) => video.video_id !== featuredVideo?.video_id,
      ),
    }))
    .filter((group) => group.videos.length > 0);
  const readyCount = visibleVideos.filter((video) =>
    ["ready", "partial"].includes(videoState(video)),
  ).length;

  const processing = visibleVideos.some(
    (video) => videoState(video) === "processing",
  );
  useEffect(() => {
    if (!session || !processing) return;
    const timer = setInterval(load, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [session, processing, load]);

  useEffect(() => {
    if (!session || !featuredVideo) {
      setFeaturedDetail(null);
      setFeaturedTimeline([]);
      setFeaturedEvidenceLoaded(false);
      return;
    }

    if (designPreview && featuredVideo.video_id === DESIGN_PREVIEW_VIDEO.video_id) {
      setFeaturedDetail(DESIGN_PREVIEW_DETAIL);
      setFeaturedTimeline(DESIGN_PREVIEW_TIMELINE);
      setFeaturedEvidenceLoaded(true);
      return;
    }

    let cancelled = false;
    setFeaturedEvidenceLoaded(false);
    Promise.allSettled([
      apiFetch<VideoDetail>(`/videos/${featuredVideo.video_id}`),
      apiFetch<{ entries: VideoTimelineEntry[] }>(
        `/videos/${featuredVideo.video_id}/timeline`,
      ),
    ]).then(([detailResult, timelineResult]) => {
      if (cancelled) return;
      setFeaturedDetail(
        detailResult.status === "fulfilled" ? detailResult.value : null,
      );
      setFeaturedTimeline(
        timelineResult.status === "fulfilled" ? timelineResult.value.entries : [],
      );
      setFeaturedEvidenceLoaded(true);
    });

    return () => {
      cancelled = true;
    };
  }, [session, featuredVideo?.video_id, designPreview]);

  const openAddLecture = useCallback(() => {
    setAddOpen(true);
    requestAnimationFrame(() => {
      document.getElementById("add-lecture")?.scrollIntoView({ block: "nearest" });
    });
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
      rail={null}
    >
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="hidden w-60 shrink-0 border-r border-sidebar-border bg-sidebar lg:block">
          <VideoLibraryRail
            readyCount={readyCount}
            total={visibleVideos.length}
            onAdd={openAddLecture}
          />
        </aside>

        <div className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl space-y-7 p-4 sm:p-6 lg:p-8">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h1 className="font-heading text-3xl font-medium tracking-tight sm:text-4xl">
                  Videos
                </h1>
                <p className="mt-2 max-w-3xl text-sm text-muted-foreground sm:text-base">
                  Lectures you can ask about — grounded in the transcript, what
                  was on screen, and any linked slides.
                </p>
              </div>
              <Button className="lg:hidden" onClick={openAddLecture}>
                <Plus aria-hidden />
                Add lecture
              </Button>
            </div>

            {error && !designPreview ? (
              <Alert variant="destructive">
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}

            {!loaded ? (
              <Skeleton className="aspect-[2.25/1] w-full rounded-xl" />
            ) : featuredVideo ? (
              <VideoFeatureCard
                video={featuredVideo}
                detail={featuredDetail}
                timeline={featuredTimeline}
                evidenceLoading={!featuredEvidenceLoaded}
                onDelete={remove}
                previewImage={designPreview ? "/video/mugensei-lecture-preview.png" : undefined}
              />
            ) : visibleVideos.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border p-10 text-center">
                <Video aria-hidden className="mx-auto mb-3 size-7 text-primary" />
                <p className="font-heading text-xl font-medium">No lectures yet</p>
                <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                  Add a YouTube lecture or video file. Mugensei will align its
                  transcript, screen evidence, and optional slides.
                </p>
                <Button className="mt-5" onClick={openAddLecture}>
                  <Plus aria-hidden />
                  Add your first lecture
                </Button>
              </div>
            ) : null}

            <Collapsible open={addOpen} onOpenChange={setAddOpen}>
              <section
                id="add-lecture"
                aria-labelledby="add-lecture-heading"
                className="rounded-xl border border-border bg-card/40"
              >
                <CollapsibleTrigger className="flex w-full items-center gap-4 px-5 py-5 text-left sm:px-6">
                  <span className="grid size-10 shrink-0 place-items-center rounded-full border border-primary/50 text-primary">
                    <Plus aria-hidden className="size-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      id="add-lecture-heading"
                      className="block font-heading text-xl font-medium"
                    >
                      Add a lecture
                    </span>
                    <span className="mt-0.5 block text-sm text-muted-foreground">
                      Import a lecture and its evidence in three clear steps.
                    </span>
                  </span>
                  <ChevronDown
                    aria-hidden
                    className={`size-5 text-muted-foreground transition-transform ${
                      addOpen ? "rotate-180" : ""
                    }`}
                  />
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="border-t border-border px-5 py-6 sm:px-6">
                    <AddVideo
                      onAdded={() => {
                        setAddOpen(false);
                        void load();
                      }}
                    />
                  </div>
                </CollapsibleContent>
              </section>
            </Collapsible>

            {remainingGroups.length > 0 ? (
              <div className="space-y-8">
                {remainingGroups.map((group) => (
                  <section key={group.key} aria-labelledby={`group-${group.key}`}>
                    <div className="mb-3">
                      <h2
                        id={`group-${group.key}`}
                        className="font-heading text-lg font-medium"
                      >
                        {group.title}
                        <span className="ml-2 font-mono text-xs font-normal text-muted-foreground tabular-nums">
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
            ) : null}
          </div>
        </div>
      </div>
    </AppShell>
  );
}
