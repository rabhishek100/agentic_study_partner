"use client";

import {
  AlertCircle,
  ArrowLeft,
  Crosshair,
  Maximize2,
  Minimize2,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { PageComposer } from "@/components/read/page-composer";
import {
  PageSelectionPopover,
  type PageSelection,
} from "@/components/read/page-selection";
import { RegionSelect } from "@/components/read/region-select";
import { SessionQuestions } from "@/components/read/session-questions";
import { SessionRecap, recapOf } from "@/components/read/session-recap";
import { StayInSourceToggle } from "@/components/read/stay-in-source";
import { PdfViewer, type PdfTarget } from "@/components/pdf";
import {
  BASE_Z_INDEX,
  SideChatError,
  useFloatingCapable,
} from "@/components/side-chat/side-chat-layer";
import { SideChatMenu } from "@/components/side-chat/side-chat-menu";
import { SideChatTurns } from "@/components/side-chat/side-chat-turns";
import { SideChatWindow } from "@/components/side-chat/side-chat-window";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useReadingSession } from "@/hooks/use-reading-session";
import { useStayInSource } from "@/hooks/use-stay-in-source";
import { useSession } from "@/hooks/use-session";
import { useSideChats } from "@/hooks/use-side-chats";
import { apiFetch } from "@/lib/api";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn, EvidenceRef } from "@/lib/types";
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
    // A thread's home here is its row in the questions panel. Floating is
    // something the reader asks for, one thread at a time, when they want a
    // second answer on screen beside the first.
    { detachedByDefault: false },
  );
  const documentRef = useRef<HTMLDivElement | null>(null);
  const [stayInSource, setStayInSource] = useStayInSource(
    session?.conversation_id ?? null,
  );
  const recap = useMemo(
    () => recapOf(sideChats.available.map((thread) => thread.anchors), chapters),
    [sideChats.available, chapters],
  );
  const [questionsOpen, setQuestionsOpen] = useState(true);
  const [chromeHidden, setChromeHidden] = useState(false);
  const [regionArmed, setRegionArmed] = useState(false);
  const [region, setRegion] = useState<PageSelection | null>(null);
  const [regionNote, setRegionNote] = useState("");
  // Which thread the panel is showing, or null while it shows the list. Held
  // here rather than derived from the window stack, which reorders on focus.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [excerpt, setExcerpt] = useState<string | null>(null);
  const canFloat = useFloatingCapable();

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
    ? {
        document: { kind: "book", bookId },
        title: session.title,
        page,
        excerpt,
      }
    : null;

  const changePageFromReader = useCallback(
    (next: number) => {
      // Turning the page by hand leaves the previous citation's highlight
      // behind, lighting a sentence the reader is no longer looking at.
      setExcerpt(null);
      changePage(next);
    },
    [changePage],
  );

  /**
   * Follow a citation into the page it came from.
   *
   * Only into *this* book: an answer that escalated to the library cites a
   * book this route is not showing, and turning to page 40 of the wrong volume
   * is worse than doing nothing. Those references still open from the answer's
   * own reference cards.
   */
  const openReference = useCallback(
    (reference: EvidenceRef, referencePage?: number) => {
      if (reference.book_id !== bookId) return;
      const targetPage = referencePage ?? reference.pages[0];
      if (!targetPage) return;
      setExcerpt(reference.excerpt ?? null);
      changePage(targetPage);
    },
    [bookId, changePage],
  );

  const ask = useCallback(
    async (question: string) => {
      if (!session) return;
      // The chip *is* the anchor. With it on, the question names the page and
      // the answer pins that page's passages first; with it off there is no
      // anchor at all and the question searches the book. Anchoring to some
      // other page to represent "no page" would be a lie the reader could not
      // see.
      const created = await sideChats.open(
        pageInContext
          ? { kind: "document_page", bookId, page, question, title: question }
          : { kind: "unanchored", question, title: question },
      );
      // Show the thread that was just asked. The answer is the thing the
      // reader is waiting for, so the panel goes to it rather than leaving
      // them to find the new row.
      if (created) setSelectedId(created.conversation_id);
    },
    [bookId, page, pageInContext, session, sideChats],
  );

  const askAboutSelection = useCallback(
    async (selectedText: string, question?: string) => {
      if (!session) return;
      const created = await sideChats.open({
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
      if (created) setSelectedId(created.conversation_id);
    },
    [bookId, page, session, sideChats],
  );

  const sourceNoun = session?.document_type === "paper" ? "paper" : "book";

  /**
   * Drawing a box on the page to ask about it.
   *
   * One element, rendered in the frame or in the document's toolbar depending
   * on which of the two the reader has on screen — never in both, so there is
   * one thing to press and one pressed state to read.
   */
  const regionTool = (
    <Button
      variant={regionArmed ? "secondary" : "ghost"}
      size="icon-sm"
      aria-pressed={regionArmed}
      aria-label={
        regionArmed
          ? "Stop selecting a region"
          : "Select a region of the page to ask about"
      }
      onClick={() => {
        setRegionArmed(!regionArmed);
        setRegionNote("");
      }}
    >
      <Crosshair aria-hidden />
    </Button>
  );

  /**
   * Every thread this session has on screen, mounted, with one showing.
   *
   * All of them stay mounted whatever the panel is showing, because an answer
   * is still arriving in most of them: unmounting a thread aborts its stream
   * and drops its place in the shared generation queue, so going back to the
   * list would throw away the answer the reader went back to wait for.
   *
   * A detached thread renders as a floating window from this same position,
   * through a portal — the component must not move in the tree, or detaching
   * mid-answer would remount it and abort exactly what the reader wanted to
   * keep watching.
   */
  const threadDetail = (
    <>
      {sideChats.windows.map((entry, index) => {
        const id = entry.sideChat.conversation_id;
        const showing = !entry.detached && id === selectedId;
        return (
          <div
            key={id}
            hidden={!showing}
            className={cn("min-h-0 flex-1 flex-col", showing ? "flex" : "hidden")}
          >
            <SideChatWindow
              docked={!entry.detached}
              portal
              window={entry}
              zIndex={BASE_Z_INDEX + index}
              onRectChange={(rect) => sideChats.setRect(id, rect)}
              // On this surface a window is a thread that stepped out, not a
              // thread that is open. Putting it away returns it to the list,
              // which is where it lives; nothing is lost and nothing is
              // stranded in a dock.
              onMinimize={() => sideChats.attach(id)}
              onClose={() => sideChats.attach(id)}
              onFocus={() => sideChats.focus(id)}
              onSettled={(recorded) => sideChats.noteSettled(id, recorded)}
              onAnchorsChange={(anchors) => {
                void sideChats.setAnchors(id, anchors);
              }}
              surface={BOOK_SIDE_CHATS}
              renderTurns={({ turns: sideTurns, isLoading: loading, isQueued }) => (
                <SideChatTurns
                  turns={sideTurns as ChatTurn[]}
                  isLoading={loading}
                  isQueued={isQueued}
                  stayInSource={stayInSource}
                  onStayInSource={() => setStayInSource(true)}
                  onOpenReference={openReference}
                />
              )}
              // Nothing in this session is quoted from an answer: every thread
              // is anchored to the source. Pasting a passage has no turn to
              // resolve against, and saying so is better than resolving it to
              // turn zero.
              resolveQuoteTurn={() => null}
              onPendingSent={() => sideChats.clearPending(id)}
              stayInSource={stayInSource}
            />
          </div>
        );
      })}
    </>
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
      hideHeader={chromeHidden}
      regions={[
        {
          key: "questions",
          // One column, not two. The margin gutter listed the same questions
          // this does, so a reader saw their session twice and could not tell
          // which one to use.
          label: "Questions in this session",
          // Draggable rather than fixed: how much of the window a reader wants
          // for the source and how much for the conversation is theirs to
          // decide, and it changes with what they are doing. Its own key and
          // range, because a chat column beside a page wants a quarter of the
          // row where a document beside a conversation wants half.
          resize: {
            storageKey: "asp:read-questions-width",
            defaultPercent: 28,
            minPercent: 18,
            maxPercent: 55,
            label: "Resize the questions pane",
          },
          node: (
            <SessionQuestions
              threads={sideChats.available}
              detachedIds={sideChats.detachedIds}
              selectedId={selectedId}
              onOpen={(thread) => {
                sideChats.show(thread);
                // A thread already in a window of its own is raised where it
                // is. Showing it in the panel as well would put one thread in
                // two places, which is the thing this panel exists to stop.
                if (sideChats.detachedIds.has(thread.conversation_id)) return;
                setSelectedId(thread.conversation_id);
              }}
              onBack={() => setSelectedId(null)}
              onDetach={
                canFloat
                  ? (thread) => {
                      sideChats.detach(thread.conversation_id);
                      setSelectedId(null);
                    }
                  : undefined
              }
              detail={threadDetail}
              hereLabel={`p. ${page}`}
              lockLabel={stayInSource ? sourceNoun : null}
              menu={
                <>
                  <SessionRecap recap={recap} bookId={bookId} />
                  <StayInSourceToggle
                    locked={stayInSource}
                    onChange={setStayInSource}
                    noun={sourceNoun}
                  />
                </>
              }
              footer={
                <PageComposer
                  page={page}
                  sectionTitle={currentChapter?.title ?? null}
                  pageInContext={pageInContext}
                  onPageInContextChange={setPageInContext}
                  disabled={!session || sideChats.isOpening}
                  onSubmit={ask}
                />
              }
            />
          ),
        },
      ]}
      activeRegion={questionsOpen ? "questions" : null}
      status={
        <span className="flex min-w-0 items-center gap-2">
          <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-positive" />
          <span className="truncate">
            {session ? `Reading · ${session.title}` : "Opening…"}
          </span>
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
      account={<AccountMenu email={authSession.user.email} />}
      sideChatControl={
        <>
          <Button
            variant="outline"
            size="sm"
            aria-pressed={questionsOpen}
            onClick={() => setQuestionsOpen(!questionsOpen)}
          >
            Questions
            <span className="font-mono text-xs tabular-nums text-muted-foreground">
              {sideChats.available.length}
            </span>
          </Button>
          {regionTool}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Hide the top bar and read full height"
            onClick={() => setChromeHidden(true)}
          >
            <Maximize2 aria-hidden />
          </Button>
          <SideChatMenu
          sideChats={sideChats.available}
          openIds={sideChats.openIds}
          onOpen={sideChats.show}
          onDelete={sideChats.remove}
          />
        </>
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
        // The threads themselves render in the questions panel, detached ones
        // included — see `threadDetail`. What is left for the overlay is the
        // failure that has no thread to attach itself to.
        sideChats.error ? (
          <SideChatError
            error={sideChats.error}
            onDismiss={sideChats.dismissError}
          />
        ) : null
      }
    >
      <div className="relative flex min-h-0 flex-1 flex-col">
        {error && (
          <div className="p-4">
            <Alert variant="destructive">
              <AlertCircle aria-hidden />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          </div>
        )}

        <div className="flex min-h-0 flex-1">
          <div ref={documentRef} className="relative min-h-0 min-w-0 flex-1">
            <RegionSelect
              container={documentRef}
              active={regionArmed}
              onRegion={(selection) => {
                setRegion(selection);
                setRegionArmed(false);
                setRegionNote("");
              }}
              onEmpty={() =>
                setRegionNote(
                  "Nothing selectable there. A figure is drawn rather than " +
                    "written, so it carries no text to quote — ask about the " +
                    "page instead.",
                )
              }
            />
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
                onPageChange={changePageFromReader}
                zoom={zoom}
                onZoomChange={setZoom}
                tools={
                  chromeHidden ? (
                    // Hiding the top bar hid the controls it carried, leaving
                    // one restore button floating over the page and no way at
                    // all to draw a region. The document's own toolbar takes
                    // them for as long as the frame is away.
                    <>
                      {regionTool}
                      <Button
                        variant="outline"
                        size="icon-sm"
                        aria-label="Show the top bar"
                        onClick={() => setChromeHidden(false)}
                      >
                        <Minimize2 aria-hidden />
                      </Button>
                    </>
                  ) : null
                }
              />
            )}
          </div>
          <PageSelectionPopover
            container={documentRef}
            sessionId={session?.conversation_id ?? null}
            bookId={bookId}
            page={page}
            onAsk={askAboutSelection}
            external={region}
            onDismissExternal={() => setRegion(null)}
          />
          {regionNote && (
            <p
              role="status"
              className="absolute bottom-3 left-1/2 z-sticky max-w-md -translate-x-1/2 rounded-lg border border-border bg-popover px-3 py-2 text-xs text-muted-foreground shadow-lg"
            >
              {regionNote}
            </p>
          )}
        </div>

      </div>
    </AppShell>
  );
}
