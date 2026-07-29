"use client";

import { useCallback, useRef, useState } from "react";

import { API_BASE } from "@/lib/api";
import { drainSseEvents } from "@/lib/sse";
import { accessToken } from "@/lib/supabase";
import type {
  ChatResponse,
  ChatTurn,
  ConversationDetail,
  ConversationState,
  ResponseDepth,
  RetrievalMode,
} from "@/lib/types";

/** How long the stream may go quiet before the client gives up on it. */
const STREAM_IDLE_TIMEOUT_MS = 60_000;

interface SendOptions {
  bookIds: number[];
  retrievalMode: RetrievalMode;
  responseDepth: ResponseDepth;
}

/**
 * Owns one conversation's turns and the SSE stream that fills them.
 *
 * Turns are addressed by a stable id generated before dispatch. The previous
 * implementation captured an array index from inside a `setState` updater,
 * which made the updater impure and tied every write to the list's length at
 * dispatch time; ids remove both problems and let a turn be updated safely
 * while other turns are added or removed.
 */
export function useChat() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [conversation, setConversation] = useState<ConversationState | null>(
    null,
  );
  // The server owns conversation state; this is only the identity the client
  // sends back to resume. Null means the next turn starts a conversation.
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);

  const controllerRef = useRef<AbortController | null>(null);
  const stoppedByUserRef = useRef(false);

  const patchTurn = useCallback((id: string, patch: Partial<ChatTurn>) => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)),
    );
  }, []);

  const send = useCallback(
    async (
      question: string,
      { bookIds, retrievalMode, responseDepth }: SendOptions,
    ) => {
      const submitted = question.trim();
      if (!submitted || controllerRef.current) return;

      const id = crypto.randomUUID();
      setTurns((current) => [
        ...current,
        {
          id,
          question: submitted,
          answer: "",
          status: "streaming",
          result: null,
          error: null,
        },
      ]);
      setIsStreaming(true);

      const controller = new AbortController();
      controllerRef.current = controller;
      stoppedByUserRef.current = false;

      let idleTimer = setTimeout(
        () => controller.abort(),
        STREAM_IDLE_TIMEOUT_MS,
      );
      const resetIdleTimer = () => {
        clearTimeout(idleTimer);
        idleTimer = setTimeout(() => controller.abort(), STREAM_IDLE_TIMEOUT_MS);
      };

      // Held locally as well as in state so the abort path can keep whatever
      // arrived before the user pressed stop.
      let streamedText = "";

      try {
        const token = await accessToken();
        const response = await fetch(`${API_BASE}/chat/stream`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify({
            question: submitted,
            retrieval_mode: retrievalMode,
            book_ids: bookIds,
            conversation_id: conversationId,
            response_depth: responseDepth,
          }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          const rawBody = await response.text();
          let parsed: string | null = null;
          try {
            parsed = rawBody
              ? ((JSON.parse(rawBody) as { detail?: string }).detail ?? null)
              : null;
          } catch {
            parsed = null;
          }
          throw new Error(
            parsed ??
              `The study request failed (${response.status}): ${
                rawBody.slice(0, 200) || response.statusText
              }`,
          );
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let settled = false;

        readLoop: while (true) {
          const { done, value } = await reader.read();
          resetIdleTimer();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const { events, rest } = drainSseEvents(buffer);
          buffer = rest;

          for (const { event, data: payload } of events) {
            if (event === "token") {
              const data = JSON.parse(payload) as { text: string };
              streamedText += data.text;
              patchTurn(id, { answer: streamedText });
            } else if (event === "final") {
              const data = JSON.parse(payload) as ChatResponse;
              settled = true;
              setConversation(data.state);
              // The server may have started a new conversation — a changed
              // book selection does exactly that — so the id always comes
              // back from the response rather than being assumed.
              setConversationId(data.state.conversation_id);
              patchTurn(id, {
                answer: data.result.answer,
                result: data.result,
                status: "complete",
              });
              break readLoop;
            } else if (event === "error") {
              const data = JSON.parse(payload) as { detail?: string };
              throw new Error(data.detail ?? "The study request failed.");
            }
          }
        }

        if (!settled) {
          throw new Error("The study API closed the stream unexpectedly.");
        }
      } catch (caught) {
        const error = caught as Error;
        if (stoppedByUserRef.current) {
          // A deliberate stop keeps whatever was generated. The server never
          // sent `final`, so conversation state correctly does not advance.
          patchTurn(id, { answer: streamedText, status: "stopped" });
        } else {
          patchTurn(id, {
            status: "failed",
            error:
              error.name === "AbortError"
                ? "The study API stopped responding and the request timed out."
                : (error.message ?? "Could not reach the study API."),
          });
        }
      } finally {
        clearTimeout(idleTimer);
        controllerRef.current = null;
        stoppedByUserRef.current = false;
        setIsStreaming(false);
      }
    },
    [conversationId, patchTurn],
  );

  const stop = useCallback(() => {
    if (!controllerRef.current) return;
    stoppedByUserRef.current = true;
    controllerRef.current.abort();
  }, []);

  /** Drop the last turn and ask its question again. */
  const retry = useCallback(
    async (options: SendOptions) => {
      const last = turns.at(-1);
      if (!last || isStreaming) return;
      setTurns((current) => current.filter((turn) => turn.id !== last.id));
      await send(last.question, options);
    },
    [turns, isStreaming, send],
  );

  /** Start a fresh conversation, leaving whatever is stored untouched. */
  const reset = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setTurns([]);
    setConversation(null);
    setConversationId(null);
    setIsStreaming(false);
  }, []);

  /** Replace the visible conversation with one loaded from the server. */
  const resume = useCallback((detail: ConversationDetail) => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setIsStreaming(false);
    setConversationId(detail.conversation_id);
    setConversation(null);
    setTurns(
      detail.turns.map((turn) => ({
        id: `${detail.conversation_id}-${turn.turn_index}`,
        question: turn.question,
        answer: turn.answer,
        status: "complete" as const,
        result: turn.result,
        error: null,
      })),
    );
  }, []);

  return {
    turns,
    conversation,
    conversationId,
    isStreaming,
    send,
    stop,
    retry,
    reset,
    resume,
  };
}
