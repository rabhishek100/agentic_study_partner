"use client";

import { LogOut, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { describeSelection } from "@/components/book-selector";
import { AuthGate } from "@/components/auth-gate";
import { ConversationView } from "@/components/conversation/conversation-view";
import { EvidencePanel } from "@/components/conversation/evidence-panel";
import { LibraryRail } from "@/components/library-rail";
import { PdfViewer, type PdfTarget } from "@/components/pdf";
import { SectionNav } from "@/components/section-nav";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatMenu } from "@/components/side-chat/side-chat-menu";
import { SideChatTurns } from "@/components/side-chat/side-chat-turns";
import { ThemeToggle } from "@/components/theme-toggle";
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
import { useChat } from "@/hooks/use-chat";
import { useConversations } from "@/hooks/use-conversations";
import { useSideChats } from "@/hooks/use-side-chats";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { takeQuestion } from "@/lib/deck-handoff";
import type {
  BookListResponse,
  BookSummary,
  ChatTurn,
  EvidenceRef,
  ResponseDepth,
  RetrievalMode,
} from "@/lib/types";

export default function Page() {
  const { session, sessionLoading } = useSession();
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [booksLoaded, setBooksLoaded] = useState(false);
  const [booksError, setBooksError] = useState("");
  const [selectedBookIds, setSelectedBookIds] = useState<number[]>([]);
  const [retrievalMode, setRetrievalMode] =
    useState<RetrievalMode>("hybrid_rerank");
  const [responseDepth, setResponseDepth] =
    useState<ResponseDepth>("interview");
  const [reading, setReading] = useState<PdfTarget | null>(null);
  const [readingMinimized, setReadingMinimized] = useState(false);
  /*
    Which turn's evidence the region shows. `null` follows the newest grounded
    answer, which is what makes the region fill on its own as the conversation
    goes; picking a turn pins it there until the reader picks another.
  */
  const [pinnedSourcesTurn, setPinnedSourcesTurn] = useState<number | null>(null);
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfZoom, setPdfZoom] = useState(1);

  const {
    turns,
    conversation,
    conversationId,
    isStreaming,
    send,
    stop,
    retry,
    reset,
    resume,
  } = useChat();
  const history = useConversations("book");
  const sideChats = useSideChats(conversationId, BOOK_SIDE_CHATS);

  const loadBooks = useCallback(async () => {
    setBooksError("");
    try {
      const payload = await apiFetch<BookListResponse>("/books");
      setBooks(payload.books);
      // Default to the whole library: cross-book study is the point, and
      // narrowing is the deliberate exception rather than the starting point.
      setSelectedBookIds((current) =>
        current.length > 0
          ? current.filter((bookId) =>
              payload.books.some((book) => book.book_id === bookId),
            )
          : payload.books.map((book) => book.book_id),
      );
    } catch (caught) {
      // "You have no books" and "the library could not be loaded" are
      // different situations and must not share one empty state.
      setBooksError(
        (caught as Error).message || "Could not load your library.",
      );
    } finally {
      setBooksLoaded(true);
    }
  }, []);

  /**
   * Rename one book.
   *
   * Titles are derived at ingestion — from the PDF's metadata, its first page,
   * or its filename read as words — and a derivation is a guess. A saved web
   * page keeps the site's chrome in its metadata title; this is how a reader
   * fixes one without a database client.
   */
  const renameBook = useCallback(
    async (bookId: number, title: string) => {
      await apiFetch(`/books/${bookId}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      });
      await loadBooks();
    },
    [loadBooks],
  );

  useEffect(() => {
    if (session) {
      loadBooks();
      history.refresh();
    }
    // `history.refresh` is stable; depending on the whole hook object would
    // re-run this on every list mutation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, loadBooks]);

  const hasBooks = books.length > 0;
  const canSend = hasBooks && selectedBookIds.length > 0;

  const closeDocument = useCallback(() => {
    setReading(null);
    setReadingMinimized(false);
    setPdfPage(1);
    setPdfZoom(1);
  }, []);

  const handleSend = useCallback(
    async (question: string, mentionedBookIds: number[] = []) => {
      const requestBookIds =
        selectedBookIds.length > 0 ? selectedBookIds : mentionedBookIds;
      if (requestBookIds.length === 0) return;
      await send(question, {
        bookIds: requestBookIds,
        mentionedBookIds,
        retrievalMode,
        responseDepth,
      });
      // The turn may have created a conversation or renamed nothing at all;
      // refreshing afterwards keeps the sidebar honest either way.
      await history.refresh();
    },
    [send, selectedBookIds, retrievalMode, responseDepth, history],
  );

  /**
   * `?book=&page=` — where a card's "open the source" lands.
   *
   * Read from `window.location` rather than `useSearchParams`, which would
   * force this page under a Suspense boundary purely to support a link that
   * is followed once and then cleared from the URL.
   */
  useEffect(() => {
    if (!session || !booksLoaded || books.length === 0) return;
    const params = new URLSearchParams(window.location.search);
    const bookId = Number(params.get("book"));
    const page = Number(params.get("page"));
    if (!bookId || !page) return;
    const book = books.find((candidate) => candidate.book_id === bookId);
    if (!book) return;
    setPdfZoom(1);
    setPdfPage(page);
    setReadingMinimized(false);
    setReading({
      document: { kind: "book", bookId },
      title: book.title,
      page,
    });
    window.history.replaceState(null, "", window.location.pathname);
  }, [session, booksLoaded, books]);

  /**
   * A card the reader could not recall, arriving from the review screen.
   *
   * Read once, after books load so the narrowing can be applied, and only for
   * a book the caller still has: a deck outlives the book it was made from
   * only if the book was deleted, and asking about it then would search the
   * whole library and answer from the wrong one.
   */
  useEffect(() => {
    if (!session || !booksLoaded || books.length === 0) return;
    const handoff = takeQuestion();
    if (!handoff) return;
    const narrowed = (handoff.bookIds ?? []).filter((bookId) =>
      books.some((book) => book.book_id === bookId),
    );
    const requestBookIds =
      narrowed.length > 0 ? narrowed : books.map((book) => book.book_id);
    setSelectedBookIds(requestBookIds);
    void send(handoff.question, {
      bookIds: requestBookIds,
      mentionedBookIds: [],
      retrievalMode,
      responseDepth,
    }).then(() => history.refresh());
    // Runs once per arrival: `takeQuestion` clears the stash, so a re-render
    // cannot re-ask it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, booksLoaded, books.length]);

  const handleOpenConversation = useCallback(
    async (id: string) => {
      const detail = await history.open(id);
      closeDocument();
      resume(detail);
      setSelectedBookIds(detail.book_ids);
      setRetrievalMode(detail.retrieval_mode);
    },
    [history, resume, closeDocument],
  );

  const handleDeleteConversation = useCallback(
    async (id: string) => {
      await history.remove(id);
      // Deleting the conversation on screen leaves nothing to continue.
      if (id === conversationId) {
        closeDocument();
        reset();
      }
    },
    [history, conversationId, reset, closeDocument],
  );

  const handleRetry = useCallback(() => {
    const retryBookIds =
      selectedBookIds.length > 0
        ? selectedBookIds
        : (conversation?.book_ids ?? []);
    if (retryBookIds.length === 0) return;
    retry({ bookIds: retryBookIds, retrievalMode, responseDepth });
  }, [
    retry,
    selectedBookIds,
    conversation,
    retrievalMode,
    responseDepth,
  ]);

  const openReference = useCallback(
    (reference: EvidenceRef, page?: number) => {
      if (reference.book_id === null) return;
      const targetPage = page ?? reference.pages[0] ?? 1;
      const opened =
        reading?.document.kind === "book" ? reading.document.bookId : null;
      if (opened !== reference.book_id) setPdfZoom(1);
      setPdfPage(targetPage);
      setReadingMinimized(false);
      setReading({
        document: { kind: "book", bookId: reference.book_id },
        title:
          reference.book_title ??
          books.find((book) => book.book_id === reference.book_id)?.title ??
          "This book",
        // The page the marker itself names, when it names one. A summary's
        // evidence can span five pages, so opening its first would land the
        // reader several pages from the sentence they clicked.
        page: targetPage,
        excerpt: reference.excerpt,
      });
    },
    [books, reading?.document],
  );

  /**
   * Which recorded turn a passage came from, matched against this
   * conversation's answers.
   *
   * Derived rather than assumed: attributing a pasted passage to whichever turn
   * the window happens to be anchored to would resolve its citation markers
   * against the wrong evidence. Whitespace is normalized on both sides because
   * copying out of rendered Markdown does not preserve the source's line breaks.
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

  const handleNewConversation = useCallback(() => {
    closeDocument();
    reset();
  }, [reset, closeDocument]);

  function selectBooks(bookIds: number[]) {
    const unchanged =
      bookIds.length === selectedBookIds.length &&
      bookIds.every((bookId, index) => bookId === selectedBookIds[index]);
    if (unchanged) return;
    closeDocument();
    setSelectedBookIds(bookIds);
    // Earlier answers were grounded in the previous selection, so a different
    // set of books is a different conversation. The server enforces the same
    // rule; resetting here keeps the interface from showing turns that the
    // next request will no longer carry.
    reset();
  }

  /*
    The right region: empty until an answer is grounded, then it fills. That is
    the composition working — the page is asymmetric because evidence occupies
    the space, not because a panel lives there.

    Both modes stay mounted; `activeRegion` decides which shows. A document wins
    while it is open, since the reader opened it from the evidence and wants to
    read it.
  */
  const groundedTurns = turns.filter(
    (turn) => turn.turnIndex != null && (turn.result?.evidence.length ?? 0) > 0,
  );
  const sourcesTurn =
    groundedTurns.find((turn) => turn.turnIndex === pinnedSourcesTurn) ??
    groundedTurns[groundedTurns.length - 1] ??
    null;
  const documentOpen = Boolean(reading) && !readingMinimized;
  const activeRegion = documentOpen
    ? "document"
    : sourcesTurn
      ? "evidence"
      : null;

  const regionsForShell = [
    {
      key: "evidence",
      label: "Evidence for this answer",
      fixedWidth: 340,
      node: (
        <EvidencePanel
          turn={sourcesTurn}
          turnNumber={
            sourcesTurn?.turnIndex != null ? sourcesTurn.turnIndex + 1 : null
          }
          onOpenReference={openReference}
        />
      ),
    },
    ...(reading
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
      : []),
  ];

  if (sessionLoading) {
    return (
      <div className="grid h-dvh overflow-y-auto place-items-center p-6">
        <div className="w-full max-w-md space-y-3" aria-hidden>
          <Skeleton className="mx-auto size-11 rounded-xl" />
          <Skeleton className="h-6 w-2/3 mx-auto" />
          <Skeleton className="h-4 w-full" />
        </div>
        <span className="sr-only" role="status">
          Loading your session…
        </span>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="relative grid h-dvh overflow-y-auto place-items-center p-6">
        {/* Reachable before sign-in: a reader who needs light mode should not
            have to authenticate first to get it. */}
        <div className="absolute right-3 top-3">
          <ThemeToggle />
        </div>
        <AuthGate />
      </div>
    );
  }

  return (
    <AppShell
      nav={<SectionNav active="books" />}
      status={
        <span className="flex items-center gap-2">
          <span
            aria-hidden
            className={
              conversation
                ? "size-1.5 rounded-full bg-positive"
                : "size-1.5 rounded-full bg-muted-foreground"
            }
          />
          {conversation ? "Conversation active" : "Ready to study"}
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
          surface={BOOK_SIDE_CHATS}
          renderTurns={({ turns: sideTurns, isLoading, isQueued }) => (
            <SideChatTurns
              turns={sideTurns as ChatTurn[]}
              isLoading={isLoading}
              isQueued={isQueued}
              onOpenReference={openReference}
            />
          )}
          onAnchorsChange={(sideChatId, anchors) => {
            void sideChats.setAnchors(sideChatId, anchors);
          }}
          resolveQuoteTurn={resolveQuoteTurn}
          error={sideChats.error}
          onDismissError={sideChats.dismissError}
        />
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
      regions={regionsForShell}
      activeRegion={activeRegion}
      rail={
        <LibraryRail
          books={books}
          booksLoaded={booksLoaded}
          booksError={booksError}
          onRetryLoadBooks={loadBooks}
          selectedBookIds={selectedBookIds}
          onSelectBooks={selectBooks}
          retrievalMode={retrievalMode}
          onRetrievalModeChange={setRetrievalMode}
          hasConversation={turns.length > 0}
          onBooksChanged={loadBooks}
        onRenameBook={renameBook}
          history={{
            conversations: history.conversations,
            loaded: history.loaded,
            activeId: conversationId,
            onOpen: handleOpenConversation,
            onRename: history.rename,
            onDelete: handleDeleteConversation,
            onNew: handleNewConversation,
          }}
        />
      }
    >
      <ConversationView
        onShowSources={(turnIndex: number) => {
          setPinnedSourcesTurn(turnIndex);
          // The reader asked for these sources; a document over them would hide
          // the thing they just asked to see.
          setReadingMinimized(true);
        }}
        shownSourcesTurn={
          activeRegion === "evidence" ? (sourcesTurn?.turnIndex ?? null) : null
        }
        books={books}
        selectedBookIds={selectedBookIds}
        onOpenReference={openReference}
        onAskOnTheSide={(turnIndex, quotedText) => {
          void sideChats.open({ parentTurnIndex: turnIndex, quotedText });
        }}
        turns={turns}
        isStreaming={isStreaming}
        hasBooks={hasBooks}
        canSend={canSend}
        onSend={handleSend}
        onStop={stop}
        onRetry={handleRetry}
        conversationId={conversationId}
        responseDepth={responseDepth}
        onResponseDepthChange={setResponseDepth}
        scopeSummary={
          hasBooks ? describeSelection(books, selectedBookIds) : null
        }
      />
    </AppShell>
  );
}
