"use client";

import { LogOut } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import {
  DeckOverview,
  type DeckFilter,
} from "@/components/decks/deck-overview";
import { ReviewSession } from "@/components/decks/review-session";
import { SectionNav } from "@/components/section-nav";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
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
import { useCardSideChats } from "@/hooks/use-card-side-chats";
import { signOut, useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn } from "@/lib/types";
import {
  type DeckDetailResponse,
  type QueueCard,
  type ReviewQueue,
} from "@/lib/deck-types";

export default function DeckDetailPage() {
  const { deckId } = useParams<{ deckId: string }>();
  const router = useRouter();
  const { session, sessionLoading } = useSession();
  const [detail, setDetail] = useState<DeckDetailResponse | null>(null);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewStartCardId, setReviewStartCardId] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [filter, setFilter] = useState<DeckFilter>("all");
  const [error, setError] = useState("");
  // One deck, so the parent conversation is unambiguous and stable for the
  // whole session — the case the shared side-chat hook is shaped for.
  const sideChats = useCardSideChats(deckId ?? null);

  const load = useCallback(async () => {
    try {
      const [payload, today] = await Promise.all([
        apiFetch<DeckDetailResponse>(`/decks/${deckId}`),
        apiFetch<ReviewQueue>(`/decks/queue?deck_id=${deckId}`),
      ]);
      setDetail(payload);
      setQueue(today);
      setError("");
    } catch (caught) {
      setError((caught as Error).message || "Could not load that deck.");
    }
  }, [deckId]);

  useEffect(() => {
    if (session) void load();
  }, [session, load]);

  const reset = useCallback(async () => {
    try {
      await apiFetch(`/decks/${deckId}/reset`, { method: "POST" });
      await load();
    } catch (caught) {
      setError((caught as Error).message || "Could not reset that deck.");
    }
  }, [deckId, load]);

  const regenerate = useCallback(async () => {
    const current = detail?.deck;
    if (
      !current ||
      current.generation_mode !== "book_extracted" ||
      current.book_id === null ||
      current.node_id === null
    ) {
      return;
    }
    setRegenerating(true);
    try {
      await apiFetch("/decks", {
        method: "POST",
        body: JSON.stringify({
          source_kind: "book",
          generation_mode: "book_extracted",
          book_id: current.book_id,
          node_id: current.node_id,
        }),
      });
      router.push("/decks");
    } catch (caught) {
      setError((caught as Error).message || "Could not regenerate that deck.");
      setRegenerating(false);
    }
  }, [detail, router]);

  const openSource = useCallback(
    (
      item: QueueCard,
      citation: QueueCard["card"]["citations"][number],
    ) => {
      if (item.video_id && citation.start_ms !== null) {
        router.push(
          `/videos/${item.video_id}?t=${Math.floor(citation.start_ms / 1000)}`,
        );
        return;
      }
      if (item.book_id && citation.page !== null) {
        router.push(`/?book=${item.book_id}&page=${citation.page}`);
      }
    },
    [router],
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

  const deck = detail?.deck;
  const dueNow = queue?.cards.length ?? 0;

  return (
    <AppShell
      nav={<SectionNav active="decks" />}
      overlay={
        <SideChatLayer
          windows={sideChats.windows}
          onRectChange={sideChats.setRect}
          onMinimize={sideChats.setMinimized}
          onClose={sideChats.close}
          onFocus={sideChats.focus}
          onSettled={sideChats.noteSettled}
          surface={BOOK_SIDE_CHATS}
          renderTurns={({ turns, isLoading, isQueued }) => (
            <SideChatTurns
              turns={turns as ChatTurn[]}
              isLoading={isLoading}
              isQueued={isQueued}
            />
          )}
          onAnchorsChange={(sideChatId, anchors) => {
            void sideChats.setAnchors(sideChatId, anchors);
          }}
          resolveQuoteTurn={() => null}
          error={sideChats.error}
          onDismissError={sideChats.dismissError}
        />
      }
      status={<span className="truncate">{deck?.title ?? "Deck"}</span>}
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
      rail={null}
    >
      <div
        className={
          reviewing
            ? "flex min-h-0 w-full flex-1 flex-col overflow-hidden"
            : "flex min-h-0 w-full flex-1 flex-col overflow-hidden"
        }
      >
        {error ? (
          <Alert variant="destructive" className="m-4 shrink-0">
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {reviewing && queue ? (
          <ReviewSession
            cards={queue.cards}
            initialCardId={reviewStartCardId}
            sourceQuestions={deck?.generation_mode === "book_extracted"}
            onFinished={() => void load()}
            onExit={() => {
              setReviewing(false);
              setReviewStartCardId(null);
            }}
            onAskSelection={(card, quotedText) => {
              void sideChats.askAboutSelection(card, quotedText);
            }}
          />
        ) : !deck ? (
          <div className="space-y-2" aria-hidden>
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : detail ? (
          <DeckOverview
            detail={detail}
            dueNow={dueNow}
            reviewableCardIds={
              queue?.cards.flatMap((item) =>
                item.card.card_id ? [item.card.card_id] : [],
              ) ?? []
            }
            filter={filter}
            regenerating={regenerating}
            onFilterChange={setFilter}
            onStartReview={() => {
              setReviewStartCardId(null);
              setReviewing(true);
            }}
            onStudyCard={(item) => {
              setReviewStartCardId(item.card.card_id);
              setReviewing(true);
            }}
            onRegenerate={() => void regenerate()}
            onReset={() => void reset()}
            onOpenSource={openSource}
          />
        ) : null}
      </div>
    </AppShell>
  );
}
