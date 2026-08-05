"use client";

import { ArrowLeft, LogOut, Maximize2, MessageSquarePlus } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { AskPane } from "@/components/video/ask-pane";
import {
  IngestionStatus,
  readinessLabel,
} from "@/components/video/ingestion-status";
import {
  ChapterList,
  ResourcePanel,
  VisualTimeline,
} from "@/components/video/side-panels";
import { AttachResource } from "@/components/video/attach-resource";
import { ResumeUpload } from "@/components/video/resume-upload";
import {
  VideoPlayer,
  type VideoPlayerHandle,
} from "@/components/video/video-player";
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
import { useResizablePane } from "@/hooks/use-resizable-pane";
import { useVideoChat } from "@/hooks/use-video-chat";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { accessToken } from "@/lib/supabase";
import { cn } from "@/lib/utils";
import type {
  VideoCitationRef,
  VideoConversationDetail,
  VideoConversationSummary,
  VideoDetail,
  VideoResource,
  VideoTimelineEntry,
} from "@/lib/video-types";

const POLL_INTERVAL_MS = 5_000;
type Panel = "resources" | "timeline" | "chapters";

// The lecture is the reference and the conversation is the work, so the
// picture starts as the smaller column. Both limits are deliberate: below a
// quarter the player is too small to read a slide from, and past two thirds
// the conversation stops being the thing the page is for.
const VIDEO_PANE = {
  storageKey: "asp:video-pane-width",
  defaultPercent: 42,
  minPercent: 25,
  maxPercent: 65,
};

export default function VideoWorkspace() {
  const parameters = useParams<{ videoId: string }>();
  const videoId = parameters.videoId;
  const { session, sessionLoading } = useSession();
  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [timeline, setTimeline] = useState<VideoTimelineEntry[]>([]);
  const [conversations, setConversations] = useState<
    VideoConversationSummary[]
  >([]);
  const [panel, setPanel] = useState<Panel>("resources");
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState("");
  const playerRef = useRef<VideoPlayerHandle>(null);

  const { turns, conversationId, isStreaming, send, stop, reset, resume } =
    useVideoChat(videoId);
  const { percent, containerRef, separatorProps } = useResizablePane({
    ...VIDEO_PANE,
    edge: "left",
    label: "Resize the lecture pane",
  });

  const loadVideo = useCallback(async () => {
    try {
      setVideo(await apiFetch<VideoDetail>(`/videos/${videoId}`));
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load this video.");
    }
  }, [videoId]);

  const loadConversations = useCallback(async () => {
    const payload = await apiFetch<{
      conversations: VideoConversationSummary[];
    }>(`/videos/${videoId}/conversations`);
    setConversations(payload.conversations);
  }, [videoId]);

  const loadTimeline = useCallback(async () => {
    const payload = await apiFetch<{ entries: VideoTimelineEntry[] }>(
      `/videos/${videoId}/timeline`,
    );
    setTimeline(payload.entries);
  }, [videoId]);

  useEffect(() => {
    if (!session) return;
    loadVideo();
    loadConversations().catch(() => undefined);
  }, [session, loadVideo, loadConversations]);

  const ready = video?.ready_for_qa ?? false;
  useEffect(() => {
    if (session && ready) loadTimeline().catch(() => undefined);
  }, [session, ready, loadTimeline]);

  const processing = video?.readiness_status === "processing";
  useEffect(() => {
    if (!session || !processing) return;
    const timer = setInterval(loadVideo, POLL_INTERVAL_MS);
    return () => clearInterval(timer);
  }, [session, processing, loadVideo]);

  const seek = useCallback((milliseconds: number) => {
    playerRef.current?.seekTo(milliseconds);
  }, []);


  const openResource = useCallback(
    async (resource?: VideoResource, page?: number) => {
      if (!resource) return;
      setPanel("resources");
      const anchor =
        page && resource.resource_kind === "pdf" ? `#page=${page}` : "";
      if (resource.resource_kind === "external_link" && resource.source_url) {
        window.open(resource.source_url, "_blank", "noopener");
        return;
      }
      // The content endpoint authenticates by bearer token, which a plain
      // link cannot carry, so the bytes are fetched and opened as a blob.
      const token = await accessToken();
      const response = await fetch(
        `/api/videos/${videoId}/resources/${resource.resource_id}/content`,
        { headers: token ? { Authorization: `Bearer ${token}` } : {} },
      );
      if (!response.ok) {
        if (resource.source_url) {
          window.open(`${resource.source_url}${anchor}`, "_blank", "noopener");
        }
        return;
      }
      const url = URL.createObjectURL(await response.blob());
      window.open(`${url}${anchor}`, "_blank", "noopener");
    },
    [videoId],
  );

  const openDocument = useCallback(
    (citation: VideoCitationRef) => {
      const resource = video?.resources.find(
        (item) => item.resource_id === citation.resource_id,
      );
      openResource(resource, citation.page_number ?? undefined);
    },
    [video, openResource],
  );

  const handleAsk = useCallback(
    async (question: string) => {
      await send(question);
      loadConversations().catch(() => undefined);
    },
    [send, loadConversations],
  );

  const rebuild = useCallback(async () => {
    if (rebuilding) return;
    setRebuilding(true);
    try {
      await apiFetch(`/videos/${videoId}/reingest`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      await loadVideo();
    } catch (caught) {
      setError((caught as Error).message || "Could not start the rebuild.");
    } finally {
      setRebuilding(false);
    }
  }, [rebuilding, videoId, loadVideo]);

  const detachResource = useCallback(
    async (resource: VideoResource) => {
      await apiFetch(`/videos/${videoId}/resources/${resource.resource_id}`, {
        method: "DELETE",
      });
      await loadVideo();
    },
    [videoId, loadVideo],
  );

  const openConversation = useCallback(
    async (id: string) => {
      const detail = await apiFetch<VideoConversationDetail>(
        `/video-conversations/${id}`,
      );
      resume(detail);
    },
    [resume],
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

  return (
    <AppShell
      nav={<SectionNav active="videos" />}
      status={
        <span>
          {video
            ? readinessLabel(video.readiness_status, video.latest_ingestion)
            : "Loading…"}
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
        <div className="flex h-full flex-col gap-3 overflow-y-auto p-4">
          <Link
            href="/videos"
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft aria-hidden className="size-4" />
            All videos
          </Link>
          <Button variant="outline" size="sm" onClick={reset}>
            <MessageSquarePlus aria-hidden />
            New conversation
          </Button>
          <h2 className="font-heading text-sm font-medium">Conversations</h2>
          {conversations.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              Questions you ask about this lecture are saved here.
            </p>
          ) : (
            <ul className="space-y-1">
              {conversations.map((item) => (
                <li key={item.conversation_id}>
                  <button
                    type="button"
                    onClick={() => openConversation(item.conversation_id)}
                    aria-current={
                      item.conversation_id === conversationId
                        ? "true"
                        : undefined
                    }
                    className={cn(
                      "w-full truncate rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent/60",
                      item.conversation_id === conversationId && "bg-accent",
                    )}
                  >
                    {item.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      }
    >
      <div
        ref={containerRef}
        className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:flex-row lg:overflow-hidden"
      >
        {/*
          A share of the row on a wide screen, full width stacked below the
          breakpoint. The width is carried as a custom property so the mobile
          rule stays a plain class rather than an inline style fighting it.
        */}
        <div
          className="flex min-h-0 w-full shrink-0 flex-col gap-3 p-4 lg:w-[var(--lecture-pane)] lg:overflow-y-auto"
          style={{ "--lecture-pane": `${percent}%` } as React.CSSProperties}
        >
          {error ? (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}
          {video ? (
            <>
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <h1 className="truncate font-heading text-base font-medium">
                    {video.title}
                  </h1>
                  <p className="text-xs text-muted-foreground">
                    {video.source_kind === "youtube" ? "YouTube" : "Uploaded"} ·{" "}
                    {video.resources.length} linked{" "}
                    {video.resources.length === 1 ? "resource" : "resources"}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Play full screen"
                  title="Play full screen"
                  onClick={() => playerRef.current?.enterFullscreen()}
                >
                  <Maximize2 aria-hidden />
                </Button>
              </div>
              <div className="shrink-0">
                <VideoPlayer
                  ref={playerRef}
                  playback={video.playback}
                  title={video.title}
                />
              </div>
              {video.ready_for_qa ? null : (
                <IngestionStatus
                  readiness={video.readiness_status}
                  ingestion={video.latest_ingestion}
                />
              )}
              {video.latest_ingestion?.status === "awaiting_upload" ? (
                <ResumeUpload
                  jobId={video.latest_ingestion.job_id}
                  expectedFilename={video.source.original_filename}
                  onUploaded={loadVideo}
                />
              ) : null}
              <div className="flex min-h-0 flex-1 flex-col gap-2">
                <div
                  className="flex gap-1"
                  role="tablist"
                  aria-label="Lecture panels"
                >
                  {(["resources", "timeline", "chapters"] as Panel[]).map(
                    (key) => (
                      <Button
                        key={key}
                        role="tab"
                        aria-selected={panel === key}
                        variant={panel === key ? "secondary" : "ghost"}
                        size="sm"
                        onClick={() => setPanel(key)}
                        className="capitalize"
                      >
                        {key}
                        {key === "chapters" && video.chapters.length ? (
                          <Badge variant="outline">
                            {video.chapters.length}
                          </Badge>
                        ) : null}
                      </Button>
                    ),
                  )}
                </div>
                <div className="min-h-48 flex-1 overflow-y-auto rounded-lg border border-border p-3">
                  {panel === "resources" ? (
                    <ResourcePanel
                      resources={video.resources}
                      onOpen={(resource, page) => openResource(resource, page)}
                      onRebuild={video.ready_for_qa ? rebuild : undefined}
                      onDetach={detachResource}
                      rebuilding={rebuilding}
                      attach={
                        <AttachResource
                          videoId={videoId}
                          onAttached={loadVideo}
                        />
                      }
                    />
                  ) : panel === "timeline" ? (
                    <VisualTimeline
                      videoId={videoId}
                      entries={timeline}
                      onSeek={seek}
                    />
                  ) : (
                    <ChapterList chapters={video.chapters} onSeek={seek} />
                  )}
                </div>
              </div>
            </>
          ) : (
            <Skeleton className="aspect-video w-full" />
          )}
        </div>

        <div
          {...separatorProps}
          className={cn(
            "hidden w-1 shrink-0 cursor-col-resize bg-border transition-colors lg:block",
            "hover:bg-primary focus-visible:bg-primary",
          )}
        />

        <section
          aria-label="Ask this lecture"
          className="flex min-h-[70vh] min-w-0 flex-1 flex-col border-t border-border lg:h-full lg:min-h-0 lg:overflow-hidden lg:border-t-0"
        >
          <AskPane
            videoId={videoId}
            turns={turns}
            conversationId={conversationId}
            isStreaming={isStreaming}
            canAsk={video?.ready_for_qa ?? false}
            blockedReason={
              video?.ready_for_qa
                ? null
                : "Questions unlock when this lecture finishes processing."
            }
            onAsk={handleAsk}
            onStop={stop}
            onSeek={seek}
            onOpenDocument={openDocument}
          />
        </section>
      </div>
    </AppShell>
  );
}
