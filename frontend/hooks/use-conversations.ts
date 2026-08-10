"use client";

import { useCallback, useState } from "react";

import { apiFetch } from "@/lib/api";
import type {
  ConversationDetail,
  ConversationListResponse,
  ConversationSummary,
} from "@/lib/types";

/** The stored conversation list, and the operations that mutate it. */
export function useConversations(documentType?: "book" | "paper") {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const endpoint = documentType
        ? `/conversations?document_type=${documentType}`
        : "/conversations";
      const payload = await apiFetch<ConversationListResponse>(endpoint);
      setConversations(payload.conversations);
    } catch (caught) {
      setError((caught as Error).message || "Could not load your conversations.");
    } finally {
      setLoaded(true);
    }
  }, [documentType]);

  const open = useCallback(async (conversationId: string) => {
    return apiFetch<ConversationDetail>(`/conversations/${conversationId}`);
  }, []);

  const rename = useCallback(
    async (conversationId: string, title: string) => {
      // Optimistic: renaming is trivially reversible and the list should not
      // visibly lag behind a keystroke the user already committed to.
      setConversations((current) =>
        current.map((conversation) =>
          conversation.conversation_id === conversationId
            ? { ...conversation, title }
            : conversation,
        ),
      );
      try {
        await apiFetch(`/conversations/${conversationId}`, {
          method: "PATCH",
          body: JSON.stringify({ title }),
        });
      } catch (caught) {
        setError((caught as Error).message || "Could not rename it.");
        await refresh();
      }
    },
    [refresh],
  );

  const remove = useCallback(
    async (conversationId: string) => {
      const previous = conversations;
      setConversations((current) =>
        current.filter(
          (conversation) => conversation.conversation_id !== conversationId,
        ),
      );
      try {
        await apiFetch<void>(`/conversations/${conversationId}`, {
          method: "DELETE",
        });
      } catch (caught) {
        // A failed delete must put the conversation back rather than leaving
        // the sidebar claiming it is gone.
        setConversations(previous);
        setError((caught as Error).message || "Could not delete it.");
      }
    },
    [conversations],
  );

  return { conversations, loaded, error, refresh, open, rename, remove };
}
