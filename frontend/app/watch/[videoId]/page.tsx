"use client";

import { AlertCircle, ArrowLeft, LogOut } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatMenu } from "@/components/side-chat/side-chat-menu";
import { ThemeToggle } from "@/components/theme-toggle";
import { MomentComposer, type Stretch } from "@/components/watch/moment-composer";
import { MomentMarks, momentMarksFor } from "@/components/watch/moment-marks";
import { VideoSideChatTurns } from "@/components/video/video-side-chat-turns";
import { PdfViewer, type PdfTarget } from "@/components/pdf";
import {
  VideoPlayer,
  type VideoPlayerHandle,
} from "@/components/video/video-player";
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
import { useSideChats } from "@/hooks/use-side-chats";
import { useWatchSession } from "@/hooks/use-watch-session";
import { apiFetch } from "@/lib/api";
import { VIDEO_SIDE_CHATS } from "@/lib/side-chat";
import { timecode } from "@/lib/timecode";
import type {
  VideoDetail,
  VideoDocumentTarget,
  VideoTurn,
} from "@/lib/video-types";
import { cn } from "@/lib/utils";

/**
 * Watching a lecture, with questions left along it.
 *
 * The read surface with time where the page was. What differs is only what
 * "here" means: a page is a place the reader is looking at and a moment is a
 * place they are listening to, so the chip carries a timestamp and a viewer
 * who wants a specific span marks one.
 */
export default function WatchPage() {
  const params = useParams<{ videoId: string }>();
  const videoId = params?.videoId ?? null;
  const { session: authSession, sessionLoading } = useSession();

  const { session, isLoading, error, noteMoment } = useWatchSession(
    authSession ? videoId : null,
  );
  const [video, setVideo] = useState<VideoDetail | null>(null);
  const [atMs, setAtMs] = useState(0);
  const [stretch, setStretch] = useState<Stretch | null>(null);
  const [stretchStart, setStretchStart] = useState<number | null>(null);
  const [momentInContext, setMomentInContext] = useState(true);
  const [resumed, setResumed] = useState(false);
  const playerRef = useRef<VideoPlayerHandle>(null);
  // A lecture answer cites slide pages separately from timestamps, so a linked
  // document has to be openable from a window even though the picture, not the
  // document, holds the width here.
  const [reading, setReading] = useState<PdfTarget | null>(null);
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfZoom, setPdfZoom] = useState(1);

  const sideChats = useSideChats(
    session?.conversation_id ?? null,
    VIDEO_SIDE_CHATS,
  );
  const marks = useMemo(
    () => momentMarksFor(sideChats.available),
    [sideChats.available],
  );

  useEffect(() => {
    if (!authSession || !videoId) return;
    let cancelled = false;
    apiFetch<VideoDetail>(`/videos/${videoId}`)
      .then((detail) => {
        if (!cancelled) setVideo(detail);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [authSession, videoId]);

  // Resuming seeks once. Without the guard it would fight the playhead it is
  // reading from, dragging the lecture back every time it reported.
  useEffect(() => {
    if (!session || !video || resumed) return;
    setResumed(true);
    const resumeAt = session.position?.timestamp_ms ?? 0;
    if (resumeAt > 0) {
      setAtMs(resumeAt);
      playerRef.current?.seekTo(resumeAt);
    }
  }, [session, video, resumed]);

  const onTimeUpdate = useCallback(
    (milliseconds: number) => {
      setAtMs(milliseconds);
      noteMoment(milliseconds);
    },
    [noteMoment],
  );

  const markStretch = useCallback(() => {
    // Two presses: the first plants the start, the second closes the span at
    // wherever the lecture has reached. A single control rather than two,
    // because the second press only makes sense after the first.
    if (stretchStart === null) {
      setStretchStart(atMs);
      return;
    }
    const startMs = Math.min(stretchStart, atMs);
    const endMs = Math.max(stretchStart, atMs);
    setStretchStart(null);
    // A span of nothing is the reader pressing twice in the same place, which
    // means the moment they are on rather than an empty stretch.
    if (endMs - startMs < 1_000) return;
    setStretch({ startMs, endMs });
  }, [atMs, stretchStart]);

  const ask = useCallback(
    (question: string) => {
      if (!session || !videoId) return;
      void sideChats.open(
        !momentInContext
          ? { kind: "unanchored", question, title: question }
          : stretch
            ? {
                kind: "lecture_stretch",
                videoId,
                startMs: stretch.startMs,
                endMs: stretch.endMs,
                question,
                title: question,
              }
            : {
                kind: "lecture_moment",
                videoId,
                timestampMs: atMs,
                question,
                title: question,
              },
      );
    },
    [atMs, momentInContext, session, sideChats, stretch, videoId],
  );

  const openDocument = useCallback(
    (target: VideoDocumentTarget) => {
      if (!videoId) return;
      const resource = video?.resources.find(
        (item) => item.resource_id === target.resourceId,
      );
      setPdfPage(target.page);
      setReading({
        document: {
          kind: "video-resource",
          videoId,
          resourceId: target.resourceId,
        },
        title: resource?.title ?? "Linked document",
        page: target.page,
        excerpt: target.excerpt ?? null,
      });
    },
    [video, videoId],
  );

  if (sessionLoading) {
    return (
      <div className="grid h-dvh place-items-center overflow-y-auto p-6">
        <div className="w-full max-w-md space-y-3" aria-hidden>
          <Skeleton className="mx-auto size-11 rounded-xl" />
          <Skeleton className="mx-auto h-6 w-2/3" />
          <Skeleton className="h-4 w-full" />
        </div>
        <span className="sr-only" role="status">
          Loading your session…
        </span>
      </div>
    );
  }

  if (!authSession) {
    return (
      <div className="relative grid h-dvh place-items-center overflow-y-auto p-6">
        <div className="absolute right-3 top-3">
          <ThemeToggle />
        </div>
        <AuthGate />
      </div>
    );
  }

  return (
    <AppShell
      railMode="drawer-only"
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
                    onClose={() => setReading(null)}
                  />
                ),
              },
            ]
          : []
      }
      activeRegion={reading ? "document" : null}
      status={
        <span className="flex items-center gap-2">
          <span aria-hidden className="size-1.5 rounded-full bg-positive" />
          {session ? `Watching · ${session.title}` : "Opening…"}
        </span>
      }
      nav={
        <Button variant="ghost" size="sm" asChild>
          <Link href="/videos">
            <ArrowLeft aria-hidden />
            Lectures
          </Link>
        </Button>
      }
      account={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" className="max-w-44">
              <span className="truncate">{authSession.user.email}</span>
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
      sideChatControl={
        <SideChatMenu
          sideChats={sideChats.available}
          openIds={sideChats.openIds}
          onOpen={sideChats.show}
          onDelete={sideChats.remove}
        />
      }
      rail={
        <nav aria-label="Chapters" className="flex h-full flex-col p-3">
          <p className="px-2 pb-2 text-eyebrow uppercase text-muted-foreground">
            Chapters
          </p>
          <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto">
            {(video?.chapters ?? []).map((chapter) => (
              <li key={chapter.start_ms}>
                <button
                  type="button"
                  onClick={() => {
                    setAtMs(chapter.start_ms);
                    playerRef.current?.seekTo(chapter.start_ms);
                  }}
                  className={cn(
                    "flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-2 text-left text-sm",
                    "hover:bg-accent focus-visible:bg-accent",
                  )}
                >
                  <span className="min-w-0 truncate">{chapter.title}</span>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                    {timecode(chapter.start_ms)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </nav>
      }
      overlay={
        <SideChatLayer
          windows={sideChats.windows}
          onRectChange={sideChats.setRect}
          onMinimize={sideChats.setMinimized}
          onClose={sideChats.close}
          onFocus={sideChats.focus}
          onSettled={sideChats.noteSettled}
          onPendingSent={sideChats.clearPending}
          surface={VIDEO_SIDE_CHATS}
          renderTurns={({ turns, isLoading: loading, isQueued }) => (
            <VideoSideChatTurns
              videoId={videoId ?? ""}
              turns={turns as VideoTurn[]}
              isLoading={loading}
              isQueued={isQueued}
              onSeek={(milliseconds) => playerRef.current?.seekTo(milliseconds)}
              onOpenDocument={openDocument}
            />
          )}
          onAnchorsChange={(sideChatId, anchors) => {
            void sideChats.setAnchors(sideChatId, anchors);
          }}
          // Every window here is anchored to the lecture rather than quoted
          // from an answer, so a pasted passage has no turn to resolve against.
          resolveQuoteTurn={() => null}
          error={sideChats.error}
          onDismissError={sideChats.dismissError}
        />
      }
    >
      <div className="flex min-h-0 flex-1 flex-col">
        {error && (
          <div className="p-4">
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <MomentMarks
            marks={marks}
            atMs={atMs}
            onOpen={sideChats.show}
            onSeek={(milliseconds) => {
              setAtMs(milliseconds);
              playerRef.current?.seekTo(milliseconds);
            }}
          />
          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4">
            {isLoading || !video ? (
              <div className="grid h-full place-items-center">
                <div className="w-full max-w-3xl space-y-3" aria-hidden>
                  <Skeleton className="aspect-video w-full rounded-lg" />
                  <Skeleton className="h-6 w-1/2" />
                </div>
                <span className="sr-only" role="status">
                  Opening the lecture…
                </span>
              </div>
            ) : (
              <div className="mx-auto w-full max-w-4xl">
                <VideoPlayer
                  ref={playerRef}
                  playback={video.playback}
                  title={video.title}
                  onTimeUpdate={onTimeUpdate}
                />
                {stretchStart !== null && (
                  <p
                    role="status"
                    className="mt-2 text-xs text-muted-foreground"
                  >
                    Marking from {timecode(stretchStart)} — play on, then mark
                    again to close the stretch.
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-divider px-4 py-3">
          <div className="mx-auto w-full max-w-4xl">
            <MomentComposer
              atMs={atMs}
              stretch={stretch}
              onMarkStretch={markStretch}
              onClearStretch={() => {
                setStretch(null);
                setStretchStart(null);
              }}
              momentInContext={momentInContext}
              onMomentInContextChange={setMomentInContext}
              disabled={!session || sideChats.isOpening}
              onSubmit={ask}
            />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
