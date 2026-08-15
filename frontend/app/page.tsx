"use client";

import { LogOut, PanelRightOpen } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { BookStudyContextBar } from "@/components/book-study-context-bar";
import { describeSelection } from "@/components/book-selector";
import { AuthGate } from "@/components/auth-gate";
import { ConversationView } from "@/components/conversation/conversation-view";
import { EvidenceIndex } from "@/components/conversation/evidence-index";
import { LibraryRail } from "@/components/library-rail";
import { DesignPreviewDocument } from "@/components/pdf/design-preview-document";
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
  TurnResult,
} from "@/lib/types";

const DESIGN_PREVIEW_BOOK: BookSummary = {
  book_id: -1,
  title: "Designing Machine Learning Systems",
  author: "Chip Huyen",
  page_count: 232,
  ready_at: "2026-08-15T00:00:00Z",
  chunk_count: 1842,
  embedding_count: 1842,
  retrieval_complete: true,
  document_type: "book",
};

const DESIGN_PREVIEW_EVIDENCE: EvidenceRef[] = [
  {
    node_id: 142,
    pages: [142],
    path: "Chapter 4 :: Data Distribution Shifts :: Training-serving skew definition and causes",
    book_id: -1,
    book_title: DESIGN_PREVIEW_BOOK.title,
    rank: 1,
    chunk_id: "preview-142",
    chunk_index: 0,
    retrieval_method: "hybrid_rerank",
    score: 0.94,
    excerpt: "Training-serving skew occurs when production data differs from the data used to train the model.",
  },
  {
    node_id: 147,
    pages: [147],
    path: "Chapter 4 :: Data Distribution Shifts :: Handling shifts: retrain, features, architecture, labeling",
    book_id: -1,
    book_title: DESIGN_PREVIEW_BOOK.title,
    rank: 2,
    chunk_id: "preview-147",
    chunk_index: 1,
    retrieval_method: "hybrid_rerank",
    score: 0.89,
    excerpt: "The response depends on the shift: retraining, feature changes, architecture changes, or relabeling may be required.",
  },
];

const DESIGN_PREVIEW_RESULT: TurnResult = {
  question: "How should an ML system handle training-serving skew?",
  answer:
    "## Training-serving skew\n\nTraining-serving skew occurs when the data distribution at inference time differs from the one seen during training. It often leads to degraded accuracy and unpredictable model behavior.\n\nThree principles help address it in practice:\n\n1. **Detect skew early and continuously.** Monitor feature and prediction distributions between training and serving using statistical tests and divergence metrics. Alert on drift before quality degrades. [S1]\n\n2. **Keep the training and serving pipelines consistent.** Align feature definitions, transformations, and data sources across environments. Treat the training pipeline as a versioned, testable artifact to minimize skew from implementation gaps. [S1]\n\n3. **Close the loop with production feedback.** Use online metrics, human feedback, and logged outcomes to retrain and recalibrate. This adapts the model to real-world shifts while controlling for feedback latency and noise. [S2]\n\nTogether, these practices improve robustness to distribution shifts and sustain model performance in production.",
  route: "retrieval_qa",
  history_dependency: "independent",
  standalone_query: "handling training-serving skew",
  resolved_scope: {
    kind: "chapter",
    book_id: -1,
    node_id: 4,
    display_path: "Chapter 4 :: Data Distribution Shifts",
    start_page: 142,
    end_page: 151,
  },
  evidence: DESIGN_PREVIEW_EVIDENCE,
  citations: [
    { marker: "[S1]", node_id: 142, page: 142, book_id: -1, evidence_rank: 1 },
    { marker: "[S2]", node_id: 147, page: 147, book_id: -1, evidence_rank: 2 },
  ],
  figures: [],
  outline_node_ids: [],
  outcome: "answer",
  retrieval_mode: "hybrid_rerank",
  warnings: [],
  answer_archetype: "system_design",
  response_depth: "interview",
  routing_reason: "The question asks for a grounded operating approach.",
  prompt_profile_version: "design-preview",
  side_context: null,
  source_type: "book_library",
};

const DESIGN_PREVIEW_TURN: ChatTurn = {
  id: "design-preview-turn",
  question: DESIGN_PREVIEW_RESULT.question,
  answer: DESIGN_PREVIEW_RESULT.answer,
  status: "complete",
  result: DESIGN_PREVIEW_RESULT,
  error: null,
  turnIndex: 0,
};

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
  const [designPreview, setDesignPreview] = useState(false);
  const autoOpenedTurn = useRef<string | null>(null);

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

  useEffect(() => {
    const preview =
      process.env.NODE_ENV === "development" &&
      new URLSearchParams(window.location.search).get("design-preview") === "books";
    setDesignPreview(preview);
    if (preview) setPdfPage(142);
  }, []);

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

  const visibleBooks = designPreview && books.length === 0 ? [DESIGN_PREVIEW_BOOK] : books;
  const visibleTurns = designPreview && turns.length === 0 ? [DESIGN_PREVIEW_TURN] : turns;
  const visibleBookIds =
    designPreview && selectedBookIds.length === 0 ? [-1] : selectedBookIds;
  const hasBooks = visibleBooks.length > 0;
  const canSend = hasBooks && visibleBookIds.length > 0;

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

  useEffect(() => {
    const latest = [...turns]
      .reverse()
      .find(
        (turn) =>
          turn.status === "complete" &&
          turn.result &&
          turn.result.evidence.length > 0 &&
          turn.result.citations.length > 0,
      );
    if (!latest?.result || autoOpenedTurn.current === latest.id || reading) return;
    const first = latest.result.evidence.find((reference) =>
      latest.result!.citations.some(
        (citation) =>
          citation.node_id === reference.node_id &&
          (citation.book_id == null || citation.book_id === reference.book_id),
      ),
    );
    if (!first || first.book_id === null) return;
    autoOpenedTurn.current = latest.id;
    openReference(first, latest.result.citations[0]?.page);
  }, [turns, reading, openReference]);

  const activeTurn = [...visibleTurns]
    .reverse()
    .find((turn) => turn.status === "complete" && turn.result?.evidence.length);
  const activeResult = activeTurn?.result ?? null;
  const activeEvidence =
    activeResult?.evidence.find((reference) => reference.pages.includes(pdfPage)) ??
    activeResult?.evidence[0] ??
    null;
  const studyMode = designPreview || Boolean(reading && !readingMinimized);

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
      contextBar={
        studyMode
          ? (
              <BookStudyContextBar
                books={visibleBooks}
                selectedBookIds={visibleBookIds}
                activeEvidence={activeEvidence}
              />
            )
          : undefined
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
      aside={
        studyMode ? (
          <div className="flex h-full min-h-0 w-full overflow-hidden">
            {designPreview ? (
              <DesignPreviewDocument page={pdfPage} onPageChange={setPdfPage} />
            ) : reading ? (
              <PdfViewer
                target={reading}
                page={pdfPage}
                onPageChange={setPdfPage}
                zoom={pdfZoom}
                onZoomChange={setPdfZoom}
                onMinimize={() => setReadingMinimized(true)}
                onClose={closeDocument}
              />
            ) : null}
            {activeResult ? (
              <EvidenceIndex
                evidence={activeResult.evidence}
                citations={activeResult.citations}
                activePage={pdfPage}
                onOpen={openReference}
              />
            ) : null}
          </div>
        ) : null
      }
      rail={
        <LibraryRail
          books={visibleBooks}
          booksLoaded={booksLoaded}
          booksError={booksError}
          onRetryLoadBooks={loadBooks}
          selectedBookIds={visibleBookIds}
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
        books={visibleBooks}
        selectedBookIds={visibleBookIds}
        onOpenReference={openReference}
        onAskOnTheSide={(turnIndex, quotedText) => {
          void sideChats.open({ parentTurnIndex: turnIndex, quotedText });
        }}
        turns={visibleTurns}
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
          hasBooks ? describeSelection(visibleBooks, visibleBookIds) : null
        }
        studyMode={studyMode}
      />
    </AppShell>
  );
}
