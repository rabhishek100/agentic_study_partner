"use client";

import { AlertCircle, ArrowLeft, LogOut } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { MarginMarks, marksFor } from "@/components/read/margin-marks";
import { PageComposer } from "@/components/read/page-composer";
import { PageSelectionPopover } from "@/components/read/page-selection";
import { PdfViewer, type PdfTarget } from "@/components/pdf";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatMenu } from "@/components/side-chat/side-chat-menu";
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
import { Skeleton } from "@/components/ui/skeleton";
import { useReadingSession } from "@/hooks/use-reading-session";
import { signOut, useSession } from "@/hooks/use-session";
import { useSideChats } from "@/hooks/use-side-chats";
import { apiFetch } from "@/lib/api";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Chapter {
  node_id: number;
  title: string;
  path_text: string;
  start_page: number;
  end_page: number;
}

/**
 * Reading a book, with questions in its margins.
 *
 * The inversion this route exists for is structural rather than decorative:
 * the document holds the width, the right region stays margin, and
 * conversation happens in floating windows anchored to where the reader was.
 * A chat column beside the document would keep the transcript as the thing
 * that scrolls and the book as a reference, which is the arrangement the
 * ask-first surface already provides.
 */
export default function ReadPage() {
  const params = useParams<{ bookId: string }>();
  const bookId = Number(params?.bookId);
  const { session: authSession, sessionLoading } = useSession();
  const readable = Number.isInteger(bookId) && bookId > 0;

  const { session, isLoading, error, notePage } = useReadingSession(
    authSession && readable ? bookId : null,
  );
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [pageInContext, setPageInContext] = useState(true);
  // Only until the session's own position has been applied once. Without the
  // guard, resuming would fight every page turn the reader makes afterwards.
  const [resumed, setResumed] = useState(false);

  const sideChats = useSideChats(
    session?.conversation_id ?? null,
    BOOK_SIDE_CHATS,
  );
  const documentRef = useRef<HTMLDivElement | null>(null);
  const marks = useMemo(() => marksFor(sideChats.available), [sideChats.available]);

  useEffect(() => {
    if (!session || resumed) return;
    setPage(session.position?.page ?? 1);
    setResumed(true);
  }, [session, resumed]);

  useEffect(() => {
    if (!authSession || !readable) return;
    let cancelled = false;
    apiFetch<{ chapters: Chapter[] }>(`/books/${bookId}/chapters`)
      .then((payload) => {
        if (!cancelled) setChapters(payload.chapters);
      })
      // The outline is a convenience: it names the section in the chip and
      // fills the rail. Reading works without it, so a failure here is not
      // worth interrupting the page for.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [authSession, bookId, readable]);

  const currentChapter = useMemo(
    () =>
      chapters.find(
        (chapter) => page >= chapter.start_page && page <= chapter.end_page,
      ) ?? null,
    [chapters, page],
  );

  const changePage = useCallback(
    (next: number) => {
      setPage(next);
      notePage(next);
    },
    [notePage],
  );

  const target: PdfTarget | null = session
    ? { document: { kind: "book", bookId }, title: session.title, page }
    : null;

  const ask = useCallback(
    (question: string) => {
      if (!session) return;
      // The chip *is* the anchor. With it on, the question names the page and
      // the answer pins that page's passages first; with it off there is no
      // anchor at all and the question searches the book. Anchoring to some
      // other page to represent "no page" would be a lie the reader could not
      // see.
      void sideChats.open(
        pageInContext
          ? { kind: "document_page", bookId, page, question, title: question }
          : { kind: "unanchored", question, title: question },
      );
    },
    [bookId, page, pageInContext, session, sideChats],
  );

  const askAboutSelection = useCallback(
    (selectedText: string, question?: string) => {
      if (!session) return;
      void sideChats.open({
        kind: "document_passage",
        bookId,
        page,
        selectedText,
        question,
        // Named by the passage when the reader has not said anything yet, and
        // by their question when they have. Either way the window's title is
        // the thing they can recognise it by in the margin.
        title: question ?? selectedText,
      });
    },
    [bookId, page, session, sideChats],
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
      status={
        <span className="flex items-center gap-2">
          <span aria-hidden className="size-1.5 rounded-full bg-positive" />
          {session ? `Reading · ${session.title}` : "Opening…"}
        </span>
      }
      nav={
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft aria-hidden />
            Library
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
        <nav aria-label="Contents" className="flex h-full flex-col p-3">
          <p className="px-2 pb-2 text-eyebrow uppercase text-muted-foreground">
            Contents
          </p>
          <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto">
            {chapters.map((chapter) => {
              const active = chapter.node_id === currentChapter?.node_id;
              return (
                <li key={chapter.node_id}>
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => changePage(chapter.start_page)}
                    className={cn(
                      "flex w-full items-baseline justify-between gap-3 rounded-md px-2 py-2 text-left text-sm",
                      "hover:bg-accent focus-visible:bg-accent",
                      active &&
                        "border-l-[3px] border-primary bg-citation-muted pl-[5px] font-medium",
                    )}
                  >
                    <span className="min-w-0 truncate">{chapter.title}</span>
                    <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                      {chapter.start_page}
                    </span>
                  </button>
                </li>
              );
            })}
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
          surface={BOOK_SIDE_CHATS}
          renderTurns={({ turns: sideTurns, isLoading: loading, isQueued }) => (
            <SideChatTurns
              turns={sideTurns as ChatTurn[]}
              isLoading={loading}
              isQueued={isQueued}
            />
          )}
          onAnchorsChange={(sideChatId, anchors) => {
            void sideChats.setAnchors(sideChatId, anchors);
          }}
          // Nothing in this session is quoted from an answer: every window is
          // anchored to the source. Pasting a passage has no turn to resolve
          // against, and saying so is better than resolving it to turn zero.
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
          <MarginMarks
            marks={marks}
            page={page}
            onOpen={sideChats.show}
            onGoToPage={changePage}
          />
          <div ref={documentRef} className="min-h-0 min-w-0 flex-1">
            {isLoading || !target ? (
              <div className="grid h-full place-items-center p-6">
                <div className="w-full max-w-lg space-y-3" aria-hidden>
                  <Skeleton className="h-6 w-1/2" />
                  <Skeleton className="h-[60vh] w-full rounded-lg" />
                </div>
                <span className="sr-only" role="status">
                  Opening the book…
                </span>
              </div>
            ) : (
              <PdfViewer
                target={target}
                page={page}
                onPageChange={changePage}
                zoom={zoom}
                onZoomChange={setZoom}
              />
            )}
          </div>
          <PageSelectionPopover
            container={documentRef}
            sessionId={session?.conversation_id ?? null}
            bookId={bookId}
            page={page}
            onAsk={askAboutSelection}
          />
        </div>

        <div className="shrink-0 border-t border-divider px-4 py-3">
          <div className="mx-auto w-full max-w-3xl">
            <PageComposer
              page={page}
              sectionTitle={currentChapter?.title ?? null}
              pageInContext={pageInContext}
              onPageInContextChange={setPageInContext}
              disabled={!session || sideChats.isOpening}
              onSubmit={ask}
            />
          </div>
        </div>
      </div>
    </AppShell>
  );
}
