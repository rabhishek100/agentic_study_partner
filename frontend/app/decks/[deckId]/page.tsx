"use client";

import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AccountMenu } from "@/components/account-menu";
import { AppShell } from "@/components/app-shell";
import { AuthGate } from "@/components/auth-gate";
import {
  DeckOverview,
  type DeckFilter,
} from "@/components/decks/deck-overview";
import { ReviewSession } from "@/components/decks/review-session";
import { SideChatLayer } from "@/components/side-chat/side-chat-layer";
import { SideChatTurns } from "@/components/side-chat/side-chat-turns";
import { ThemeToggle } from "@/components/theme-toggle";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useCardSideChats } from "@/hooks/use-card-side-chats";
import { useSession } from "@/hooks/use-session";
import { apiFetch } from "@/lib/api";
import { BOOK_SIDE_CHATS } from "@/lib/side-chat";
import type { ChatTurn } from "@/lib/types";
import {
  type DeckDetailResponse,
  type QueueCard,
  type ReviewQueue,
  nextSetRequest,
} from "@/lib/deck-types";

export default function DeckDetailPage() {
  const { deckId } = useParams<{ deckId: string }>();
  const router = useRouter();
  const { session, sessionLoading } = useSession();
  const [detail, setDetail] = useState<DeckDetailResponse | null>(null);
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [reviewStartCardId, setReviewStartCardId] = useState<string | null>(null);
  const [generatingSet, setGeneratingSet] = useState(false);
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

  const generateSet = useCallback(async () => {
    const current = detail?.deck;
    if (!current) return;
    const request = nextSetRequest(current);
    if (!request) return;
    setGeneratingSet(true);
    try {
      await apiFetch("/decks", {
        method: "POST",
        body: JSON.stringify(request),
      });
      router.push("/decks");
    } catch (caught) {
      setError((caught as Error).message || "Could not start the next set.");
      setGeneratingSet(false);
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
      section="decks"
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
      account={<AccountMenu email={session.user.email} />}
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
            generatingSet={generatingSet}
            onFilterChange={setFilter}
            onStartReview={() => {
              setReviewStartCardId(null);
              setReviewing(true);
            }}
            onStudyCard={(item) => {
              setReviewStartCardId(item.card.card_id);
              setReviewing(true);
            }}
            onGenerateSet={() => void generateSet()}
            onReset={() => void reset()}
            onOpenSource={openSource}
          />
        ) : null}
      </div>
    </AppShell>
  );
}
