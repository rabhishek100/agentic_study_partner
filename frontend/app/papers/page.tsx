"use client";

import { LogOut, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { describeSelection } from "@/components/book-selector";
import { AuthGate } from "@/components/auth-gate";
import { ConversationView } from "@/components/conversation/conversation-view";
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

export default function PapersPage() {
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
  const sideChats = useSideChats(conversationId, BOOK_SIDE_CHATS);

  const loadBooks = useCallback(async () => {
    setBooksError("");
    try {
      const payload = await apiFetch<BookListResponse>("/papers");
      setBooks(payload.books);
      setSelectedBookIds((current) =>
        current.length > 0
          ? current.filter((bookId) =>
              payload.books.some((book) => book.book_id === bookId),
            )
          : payload.books.map((book) => book.book_id),
      );
    } catch (caught) {
      setBooksError(
        (caught as Error).message || "Could not load your paper library.",
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
      await history.refresh();
    },
    [send, selectedBookIds, retrievalMode, responseDepth, history],
  );

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
      retrievalMode,
      responseDepth,
    });
  }, [session, booksLoaded, books, send, retrievalMode, responseDepth]);

  const openDocumentAt = useCallback(
    (target: { bookId: number; page: number }) => {
      const book = books.find(
        (candidate) => candidate.book_id === target.bookId,
      );

      setPdfZoom(1);
      setPdfPage(target.page);
      setReadingMinimized(false);
      setReading({
        document: { kind: "book", bookId: target.bookId },
        title: book?.title ?? "Scientific Paper",
        page: target.page,
      });
    },
    [books],
  );

  const openEvidenceInDocument = useCallback(
    (evidence: EvidenceRef) => {
      if (!evidence.book_id) return;
      const page = evidence.pages[0] ?? 1;
      openDocumentAt({ bookId: evidence.book_id, page });
    },
    [openDocumentAt],
  );

  const handleSelectConversation = useCallback(
    (id: string) => {
      const target = history.conversations.find(
        (item) => item.conversation_id === id,
      );
      if (target?.book_ids && target.book_ids.length > 0) {
        const matched = target.book_ids.filter((bookId) =>
          books.some((b) => b.book_id === bookId),
        );
        if (matched.length > 0) {
          setSelectedBookIds(matched);
        }
      }
      void resume(id);
    },
    [history.conversations, books, resume],
  );

  const statusContent = !sessionLoading ? (
    <span>
      {booksLoaded ? (
        hasBooks ? (
          describeSelection(books, selectedBookIds)
        ) : (
          "No scientific papers loaded"
        )
      ) : (
        "Loading papers..."
      )}
    </span>
  ) : (
    <Skeleton className="h-4 w-32 inline-block align-middle" />
  );

  const documentControl =
    reading && readingMinimized ? (
      <Button
        variant="outline"
        size="sm"
        onClick={() => setReadingMinimized(false)}
        className="gap-1.5"
      >
        <PanelRightOpen className="size-4" />
        <span className="truncate max-w-32">{reading.title}</span>
      </Button>
    ) : null;

  const sideChatControl = sideChats.hasSideChats ? (
    <SideChatMenu
      sideChats={sideChats.sideChats}
      activeSideChatId={sideChats.activeSideChatId}
      onSelectSideChat={sideChats.openSideChat}
    />
  ) : null;

  const accountContent = session ? (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2 font-normal">
          <span className="max-w-28 truncate">{session.user.email}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="font-normal text-xs text-muted-foreground">
          Signed in as <span className="font-medium text-foreground">{session.user.email}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => void signOut()}>
          <LogOut className="size-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ) : null;

  return (
    <AuthGate>
      <AppShell
        rail={
          <LibraryRail
            books={books}
            booksLoaded={booksLoaded}
            booksError={booksError}
            onRetryLoadBooks={loadBooks}
            selectedBookIds={selectedBookIds}
            onSelectBooks={setSelectedBookIds}
            retrievalMode={retrievalMode}
            onRetrievalModeChange={setRetrievalMode}
            hasConversation={turns.length > 0}
            onBooksChanged={loadBooks}
            documentType="paper"
            history={{
              conversations: history.conversations,
              activeConversationId: conversationId,
              loaded: history.loaded,
              error: history.error,
              onSelectConversation: handleSelectConversation,
              onNewConversation: reset,
              onDeleteConversation: history.remove,
              onRenameConversation: history.rename,
            }}
          />
        }
        nav={<SectionNav active="papers" />}
        status={statusContent}
        account={accountContent}
        documentControl={documentControl}
        sideChatControl={sideChatControl}
        aside={
          reading && !readingMinimized ? (
            <PdfViewer
              target={reading}
              page={pdfPage}
              zoom={pdfZoom}
              onPageChange={setPdfPage}
              onZoomChange={setPdfZoom}
              onClose={closeDocument}
              onMinimize={() => setReadingMinimized(true)}
            />
          ) : undefined
        }
        overlay={
          sideChats.hasSideChats ? (
            <SideChatLayer
              sideChats={sideChats.sideChats}
              activeSideChatId={sideChats.activeSideChatId}
              minimizedSideChatIds={sideChats.minimizedSideChatIds}
              busySideChatId={sideChats.busySideChatId}
              onCloseSideChat={sideChats.closeSideChat}
              onMinimizeSideChat={sideChats.minimizeSideChat}
              onRestoreSideChat={sideChats.restoreSideChat}
              onFocusSideChat={sideChats.focusSideChat}
              onOpenEvidence={openEvidenceInDocument}
              renderTurns={(sideChat) => (
                <SideChatTurns
                  sideChat={sideChat}
                  isBusy={sideChats.busySideChatId === sideChat.side_chat_id}
                  onSendQuestion={(question) =>
                    sideChats.sendQuestion(sideChat.side_chat_id, question)
                  }
                  onOpenEvidence={openEvidenceInDocument}
                />
              )}
            />
          ) : undefined
        }
      >
        <ConversationView
          turns={turns as ChatTurn[]}
          isStreaming={isStreaming}
          canSend={canSend}
          noBooksConfigured={!hasBooks}
          hasBooksSelected={selectedBookIds.length > 0}
          booksCount={books.length}
          retrievalMode={retrievalMode}
          responseDepth={responseDepth}
          onResponseDepthChange={setResponseDepth}
          onSend={handleSend}
          onStop={stop}
          onRetry={retry}
          onNewConversation={reset}
          onOpenEvidence={openEvidenceInDocument}
          onOpenDocumentAt={openDocumentAt}
          onStartSideChat={sideChats.startSideChat}
        />
      </AppShell>
    </AuthGate>
  );
}
