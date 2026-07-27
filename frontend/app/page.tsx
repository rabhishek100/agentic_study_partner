"use client";

import { LogOut } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import { ConversationView } from "@/components/conversation/conversation-view";
import { LibraryRail } from "@/components/library-rail";
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
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import type { BookListResponse, BookSummary, RetrievalMode } from "@/lib/types";

export default function Page() {
  const { session, sessionLoading } = useSession();
  const [books, setBooks] = useState<BookSummary[]>([]);
  const [booksLoaded, setBooksLoaded] = useState(false);
  const [booksError, setBooksError] = useState("");
  const [selectedBookId, setSelectedBookId] = useState<number | null>(null);
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>("hybrid");

  const { turns, conversation, isStreaming, send, stop, retry, reset } =
    useChat();

  const loadBooks = useCallback(async () => {
    setBooksError("");
    try {
      const payload = await apiFetch<BookListResponse>("/books");
      setBooks(payload.books);
      setSelectedBookId(
        (current) => current ?? (payload.books[0]?.book_id ?? null),
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
    if (session) loadBooks();
  }, [session, loadBooks]);

  const activeScope = useMemo(
    () => conversation?.active_scope?.display_path ?? "No active scope",
    [conversation],
  );

  const lastResult = useMemo(
    () => turns.filter((turn) => turn.result).at(-1)?.result ?? null,
    [turns],
  );

  const hasBooks = books.length > 0;
  const canSend = hasBooks && selectedBookId !== null;

  const handleSend = useCallback(
    (question: string) => {
      if (selectedBookId === null) return;
      send(question, { bookId: selectedBookId, retrievalMode });
    },
    [send, selectedBookId, retrievalMode],
  );

  const handleRetry = useCallback(() => {
    if (selectedBookId === null) return;
    retry({ bookId: selectedBookId, retrievalMode });
  }, [retry, selectedBookId, retrievalMode]);

  function selectBook(bookId: number) {
    if (bookId === selectedBookId) return;
    setSelectedBookId(bookId);
    // Conversation state is scoped to one book; switching starts fresh.
    reset();
  }

  if (sessionLoading) {
    return (
      <div className="grid min-h-dvh place-items-center p-6">
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
      <div className="relative grid min-h-dvh place-items-center p-6">
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
      rail={
        <LibraryRail
          books={books}
          booksLoaded={booksLoaded}
          booksError={booksError}
          onRetryLoadBooks={loadBooks}
          selectedBookId={selectedBookId}
          onSelectBook={selectBook}
          retrievalMode={retrievalMode}
          onRetrievalModeChange={setRetrievalMode}
          lastResult={lastResult}
          activeScope={activeScope}
          hasConversation={turns.length > 0}
          onClearConversation={reset}
          onBooksChanged={loadBooks}
        />
      }
    >
      <ConversationView
        turns={turns}
        isStreaming={isStreaming}
        hasBooks={hasBooks}
        canSend={canSend}
        onSend={handleSend}
        onStop={stop}
        onRetry={handleRetry}
      />
    </AppShell>
  );
}
