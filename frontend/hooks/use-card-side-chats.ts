"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useSideChats } from "@/hooks/use-side-chats";
import { apiFetch } from "@/lib/api";
import type { DeckConversationResponse, QueueCard } from "@/lib/deck-types";
import { BOOK_SIDE_CHATS, type SideChatThread } from "@/lib/side-chat";

/**
 * Side chats over flashcards, bound to the deck of the card in front of you.
 *
 * A deck's cards live in one conversation, and `useSideChats` hangs off one
 * parent — so reviewing a queue that spans several decks re-binds as you cross
 * from one deck to the next, and open windows close at that boundary. They are
 * persisted, so they reopen from the thread list; keeping several decks' worth
 * of windows live at once would need the hook to hold many parents, which is a
 * change to the shared machinery rather than to this feature.
 */
export function useCardSideChats(deckId: string | null) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [openError, setOpenError] = useState("");
  const sideChats = useSideChats(conversationId, BOOK_SIDE_CHATS);
  /**
   * A thread waiting for the hook to finish binding to its parent.
   *
   * `useSideChats` clears its open windows whenever the parent conversation
   * changes, which is right for navigating between conversations and wrong
   * here: the first highlight on a deck learns the parent *from* the thread it
   * just created, so showing the window immediately had it wiped a moment
   * later by that very rebinding. The window opened and vanished.
   */
  const pending = useRef<SideChatThread | null>(null);

  useEffect(() => {
    if (!deckId) {
      setConversationId(null);
      return;
    }
    let cancelled = false;
    apiFetch<DeckConversationResponse>(`/decks/${deckId}/conversation`)
      .then((payload) => {
        if (!cancelled) setConversationId(payload.conversation_id);
      })
      .catch(() => {
        // A lecture deck has no card conversation yet, and a deck that has
        // gone away is not worth an error banner over a feature the reader has
        // not used. Highlighting simply offers nothing.
        if (!cancelled) setConversationId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [deckId]);

  /**
   * Open a side chat over a passage of one card.
   *
   * Posted to the card's own endpoint rather than through the hook's `open`,
   * because the parent turn does not exist until the server writes the card
   * down — so the client cannot supply the turn index the ordinary create
   * request requires. What comes back is an ordinary thread, handed to the
   * same `show` every other side chat uses.
   */
  const askAboutSelection = useCallback(
    async (card: QueueCard, quotedText: string) => {
      setOpenError("");
      try {
        const thread = await apiFetch<SideChatThread>(
          `/decks/cards/${card.card.card_id}/side-chats`,
          { method: "POST", body: JSON.stringify({ quoted_text: quotedText }) },
        );
        if (conversationId === thread.parent_conversation_id) {
          sideChats.show(thread);
        } else {
          pending.current = thread;
          setConversationId(thread.parent_conversation_id);
        }
        return thread;
      } catch (caught) {
        setOpenError(
          (caught as Error).message || "That side chat could not be opened.",
        );
        return null;
      }
    },
    [conversationId, sideChats],
  );

  // Runs after the hook above has rebound and cleared, so the window it opens
  // survives. Declared after `useSideChats` for exactly that ordering.
  useEffect(() => {
    const waiting = pending.current;
    if (!waiting || conversationId !== waiting.parent_conversation_id) return;
    pending.current = null;
    sideChats.show(waiting);
  }, [conversationId, sideChats]);

  const dismissError = useCallback(() => {
    setOpenError("");
    sideChats.dismissError();
  }, [sideChats]);

  return {
    ...sideChats,
    conversationId,
    askAboutSelection,
    error: openError || sideChats.error,
    dismissError,
  };
}
