"use client";

import { ArrowLeft, LogOut, Maximize2, PanelRightOpen } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { ConversationHistory } from "@/components/conversation-history";
import { SectionNav } from "@/components/section-nav";
import { ThemeToggle } from "@/components/theme-toggle";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatMenu } from "@/components/side-chat/side-chat-menu";
import { AskPane } from "@/components/video/ask-pane";
import { VideoSideChatTurns } from "@/components/video/video-side-chat-turns";
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
import { PdfViewer, type PdfTarget } from "@/components/pdf";
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
import { useSideChats } from "@/hooks/use-side-chats";
import { useVideoChat } from "@/hooks/use-video-chat";
import { VIDEO_SIDE_CHATS } from "@/lib/side-chat";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { takeQuestion } from "@/lib/deck-handoff";
import { cn } from "@/lib/utils";
import type { SideChatTurn } from "@/lib/side-chat";
import type {
  VideoConversationDetail,
  VideoConversationSummary,
  VideoDetail,
  VideoDocumentTarget,
  VideoResource,
  VideoTimelineEntry,
  VideoTurnResult,
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
  const [conversationsLoaded, setConversationsLoaded] = useState(false);
  const [panel, setPanel] = useState<Panel>("resources");
  const [rebuilding, setRebuilding] = useState(false);
  const [error, setError] = useState("");
  const [reading, setReading] = useState<PdfTarget | null>(null);
  const [readingMinimized, setReadingMinimized] = useState(false);
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfZoom, setPdfZoom] = useState(1);
  const playerRef = useRef<VideoPlayerHandle>(null);

  const { turns, conversationId, isStreaming, send, stop, retry, reset, resume } =
    useVideoChat(videoId);
  const sideChats = useSideChats(conversationId, VIDEO_SIDE_CHATS);

  /**
   * Which recorded turn a passage came from, matched against this lecture
   * conversation's answers. Derived rather than assumed, for the same reason as
   * the book chat: the turn index is what a quote's markers resolve against.
   */
  const resolveQuoteTurn = useCallback(
    (text: string) => {
      const flatten = (value: string) => value.replace(/\s+/g, " ").trim();
      const needle = flatten(text);
      if (!needle) return null;
      const match = turns.find(
        (turn) =>
          turn.turnIndex != null && flatten(turn.answer).includes(needle),
      );
      return match?.turnIndex ?? null;
    },
    [turns],
  );
  /*
    A lecture page already splits its canvas — player on the left, the ask pane
    on the right. Opening a linked document adds the right region beside both,
    and with the rail that is four columns: at 1500px the ask pane collapsed to
    roughly one character per line.

    The right region stays singular; it is the canvas that yields. While a
    document is open the canvas stacks the player above the ask pane instead of
    splitting, which is the same recomposition it already performs below `lg`.
  */
  const documentOpen = Boolean(reading) && !readingMinimized;
  const { percent, containerRef, separatorProps } = useResizablePane({
    ...VIDEO_PANE,
    edge: "left",
    label: "Resize the lecture pane",
    enabled: !documentOpen,
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
    setConversationsLoaded(true);
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

  /**
   * Arrivals from a flashcard: `?t=` seeks the player, and a stashed question
   * asks the lecture about a card the reader could not recall.
   *
   * Both wait for the lecture to load, because seeking a player that has not
   * mounted does nothing and would look like the link was broken.
   */
  useEffect(() => {
    if (!session || !video) return;
    const seconds = Number(
      new URLSearchParams(window.location.search).get("t"),
    );
    if (seconds > 0) {
      seek(seconds * 1000);
      window.history.replaceState(null, "", window.location.pathname);
    }
    const handoff = takeQuestion();
    if (handoff) {
      void send(handoff.question).then(() => loadConversations());
    }
    // Runs once the lecture is on screen; `takeQuestion` clears the stash so
    // a re-render cannot re-ask it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, Boolean(video)]);


  const closeDocument = useCallback(() => {
    setReading(null);
    setReadingMinimized(false);
    setPdfPage(1);
    setPdfZoom(1);
  }, []);

  /**
   * Open a linked document beside the conversation.
   *
   * This used to download the whole PDF and hand a blob URL to a new browser
   * tab, which lost the reader's place in the lecture, could not highlight the
   * cited passage, and left the answer behind. It is now the same docked
   * viewer the book side uses, opened at the page the citation names.
   */
  const openDocument = useCallback(
    (target: VideoDocumentTarget) => {
      const resource = video?.resources.find(
        (item) => item.resource_id === target.resourceId,
      );
      if (!resource) return;
      // A link is somewhere else on the web; there is nothing to render here.
      if (resource.resource_kind === "external_link") {
        if (resource.source_url) {
          window.open(resource.source_url, "_blank", "noopener");
        }
        return;
      }
      const opened =
        reading?.document.kind === "video-resource"
          ? reading.document.resourceId
          : null;
      if (opened !== resource.resource_id) setPdfZoom(1);
      setPdfPage(target.page);
      setReadingMinimized(false);
      setReading({
        document: {
          kind: "video-resource",
          videoId,
          resourceId: resource.resource_id,
        },
        title: resource.title,
        page: target.page,
        excerpt: target.excerpt,
      });
    },
    [video, videoId, reading?.document],
  );

  const openResource = useCallback(
    (resource: VideoResource, page?: number) =>
      openDocument({ resourceId: resource.resource_id, page: page ?? 1 }),
    [openDocument],
  );

  const handleAsk = useCallback(
    async (question: string) => {
      await send(question);
      loadConversations().catch(() => undefined);
    },
    [send, loadConversations],
  );

  const handleRetry = useCallback(async () => {
    await retry();
    loadConversations().catch(() => undefined);
  }, [retry, loadConversations]);

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
      if (
        reading?.document.kind === "video-resource" &&
        reading.document.resourceId === resource.resource_id
      ) {
        closeDocument();
      }
      await loadVideo();
    },
    [videoId, loadVideo, reading?.document, closeDocument],
  );

  const renameConversation = useCallback(
    async (id: string, title: string) => {
      await apiFetch(`/video-conversations/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      await loadConversations();
    },
    [loadConversations],
  );

  const deleteConversation = useCallback(
    async (id: string) => {
      await apiFetch(`/video-conversations/${id}`, { method: "DELETE" });
      // Deleting the conversation on screen leaves nothing to continue, and
      // its document pane nothing to belong to.
      if (id === conversationId) {
        closeDocument();
        reset();
      }
      await loadConversations();
    },
    [conversationId, closeDocument, reset, loadConversations],
  );

  const openConversation = useCallback(
    async (id: string) => {
      const detail = await apiFetch<VideoConversationDetail>(
        `/video-conversations/${id}`,
      );
      closeDocument();
      resume(detail);
    },
    [resume, closeDocument],
  );

  /**
   * `?conversation=` — where the library's Continue shortcut lands.
   *
   * Opening the lecture and leaving the reader in an empty workspace beside a
   * history panel is not continuing; it is asking them to remember, which is
   * the thing the shortcut exists to remove. Read from `window.location` for
   * the same reason as `?t=` above, and cleared once used so a refresh cannot
   * re-resume over whatever they have done since.
   */
  const resumedFromLink = useRef(false);
  useEffect(() => {
    if (!session || !video || resumedFromLink.current) return;
    const id = new URLSearchParams(window.location.search).get("conversation");
    if (!id) return;
    resumedFromLink.current = true;
    void openConversation(id)
      // A conversation deleted since the link was rendered leaves the reader
      // in a new one, which is where they would have landed anyway.
      .catch(() => undefined)
      .finally(() =>
        window.history.replaceState(null, "", window.location.pathname),
      );
  }, [session, video, openConversation]);

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
      sideChatControl={
        <SideChatMenu
          sideChats={sideChats.available}
          openIds={sideChats.openIds}
          onOpen={sideChats.show}
          onDelete={sideChats.remove}
        />
      }
      overlay={
        <SideChatLayer
          windows={sideChats.windows}
          onRectChange={sideChats.setRect}
          onMinimize={sideChats.setMinimized}
          onClose={sideChats.close}
          onFocus={sideChats.focus}
          onSettled={sideChats.noteSettled}
          onAnchorsChange={(sideChatId, anchors) => {
            void sideChats.setAnchors(sideChatId, anchors);
          }}
          surface={VIDEO_SIDE_CHATS}
          renderTurns={({ turns: sideTurns, isLoading, isQueued }) => (
            <VideoSideChatTurns
              videoId={videoId}
              turns={sideTurns as SideChatTurn<VideoTurnResult>[]}
              isLoading={isLoading}
              isQueued={isQueued}
              onSeek={seek}
              onOpenDocument={openDocument}
            />
          )}
          resolveQuoteTurn={resolveQuoteTurn}
          error={sideChats.error}
          onDismissError={sideChats.dismissError}
        />
      }
      status={
        <span>
          {video
            ? readinessLabel(video.readiness_status, video.latest_ingestion)
            : "Loading…"}
        </span>
      }
      documentControl={
        reading && readingMinimized ? (
          <Button
            variant="outline"
            size="sm"
            className="max-w-56"
            aria-label={`Restore ${reading.title} at page ${pdfPage}`}
            title={reading.title}
            onClick={() => setReadingMinimized(false)}
          >
            <PanelRightOpen aria-hidden />
            <span className="hidden max-w-32 truncate lg:inline">
              {reading.title}
            </span>
            <span className="text-muted-foreground">p. {pdfPage}</span>
          </Button>
        ) : null
      }
      /*
        A lecture has no evidence region of its own — its sources are the
        transcript and frames, which live in the canvas. The right region carries
        the linked document only.
      */
      regions={
        reading
          ? [
              {
                key: "document",
                label: `${reading.title}, source document`,
                node: (
                  <PdfViewer
                    target={reading}
                    page={pdfPage}
                    onPageChange={setPdfPage}
                    zoom={pdfZoom}
                    onZoomChange={setPdfZoom}
                    onMinimize={() => setReadingMinimized(true)}
                    onClose={closeDocument}
                  />
                ),
              },
            ]
          : []
      }
      activeRegion={documentOpen ? "document" : null}
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
            className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft aria-hidden className="size-4" />
            All videos
          </Link>
          <ConversationHistory
            conversations={conversations}
            loaded={conversationsLoaded}
            activeId={conversationId}
            onOpen={openConversation}
            onRename={renameConversation}
            onDelete={deleteConversation}
            onNew={() => {
              closeDocument();
              reset();
            }}
          />
        </div>
      }
    >
      <div
        ref={containerRef}
        className={cn(
          "flex min-h-0 flex-1 flex-col overflow-y-auto",
          !documentOpen && "lg:flex-row lg:overflow-hidden",
        )}
      >
        {/*
          A share of the row on a wide screen, full width stacked below the
          breakpoint. The width is carried as a custom property so the mobile
          rule stays a plain class rather than an inline style fighting it.
        */}
        <div
          className={cn(
            "flex min-h-0 w-full shrink-0 flex-col gap-3 p-4",
            !documentOpen && "lg:w-[var(--lecture-pane)] lg:overflow-y-auto",
          )}
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
                  <h1 className="truncate font-serif text-base font-medium">
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
                  onPlaybackError={loadVideo}
                />
              </div>
              {/* A lecture that answers but missed a quality gate still owes
                  the reader the reason, where they are working. */}
              {video.ready_for_qa && video.readiness_notes.length === 0 ? null : (
                <IngestionStatus
                  readiness={video.readiness_status}
                  ingestion={video.latest_ingestion}
                  notes={video.readiness_notes}
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
            "hidden w-1 shrink-0 cursor-col-resize bg-border transition-colors",
            "hover:bg-primary focus-visible:bg-primary",
            !documentOpen && "lg:block",
          )}
        />

        <section
          aria-label="Ask this lecture"
          className={cn(
            "flex min-h-[70vh] min-w-0 flex-1 flex-col border-t border-border",
            !documentOpen && "lg:h-full lg:min-h-0 lg:overflow-hidden lg:border-t-0",
          )}
        >
          <AskPane
            videoId={videoId}
            turns={turns}
            chapters={video?.chapters ?? []}
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
            onRetry={handleRetry}
            onSeek={seek}
            onOpenDocument={openDocument}
            onAskOnTheSide={(turnIndex, quotedText) => {
              void sideChats.open({ parentTurnIndex: turnIndex, quotedText });
            }}
          />
        </section>
      </div>
    </AppShell>
  );
}
