"use client";

import { LogOut, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { describeSelection } from "@/components/book-selector";
import { AuthGate } from "@/components/auth-gate";
import { ConversationView } from "@/components/conversation/conversation-view";
import { LibraryRail } from "@/components/library-rail";
import { PdfViewer, type PdfTarget } from "@/components/pdf";
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
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import type {
  BookListResponse,
  BookSummary,
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
  const history = useConversations();

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
      if (reading?.bookId !== reference.book_id) setPdfZoom(1);
      setPdfPage(targetPage);
      setReadingMinimized(false);
      setReading({
        bookId: reference.book_id,
        bookTitle:
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
    [books, reading?.bookId],
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
      status={
        <span className="flex items-center gap-1.5">
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
      documentControl={
        reading && readingMinimized ? (
          <Button
            variant="outline"
            size="sm"
            className="max-w-56"
            aria-label={`Restore ${reading.bookTitle} at page ${pdfPage}`}
            title={reading.bookTitle}
            onClick={() => setReadingMinimized(false)}
          >
            <PanelRightOpen aria-hidden />
            <span className="hidden max-w-32 truncate lg:inline">
              {reading.bookTitle}
            </span>
            <span className="text-muted-foreground">p. {pdfPage}</span>
          </Button>
        ) : null
      }
      aside={
        reading && !readingMinimized ? (
          <PdfViewer
            target={reading}
            page={pdfPage}
            onPageChange={setPdfPage}
            zoom={pdfZoom}
            onZoomChange={setPdfZoom}
            onMinimize={() => setReadingMinimized(true)}
            onClose={closeDocument}
          />
        ) : null
      }
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
        books={books}
        onOpenReference={openReference}
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
