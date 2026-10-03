"use client";

import { track, trackedFetch } from "@/lib/analytics";

import { useCallback, useRef, useState } from "react";

import { API_BASE, apiFetch } from "@/lib/api";
import { drainSseEvents } from "@/lib/sse";
import { accessToken } from "@/lib/supabase";
import type {
  VideoAskResponse,
  VideoConversationDetail,
  VideoConversationSummary,
  VideoTurn,
} from "@/lib/video-types";

/** How long the stream may go quiet before the client gives up on it. */
const STREAM_IDLE_TIMEOUT_MS = 90_000;

/**
 * Owns one video conversation's turns and the stream that fills them.
 *
 * Deliberately the same shape as the book chat hook: turns addressed by a
 * stable client id, the server owning conversation state, and one `final`
 * event settling the turn. Only the endpoints and the result type differ.
 */
export function useVideoChat(videoId: string) {
  const [turns, setTurns] = useState<VideoTurn[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [isStreaming, setIsStreaming] = useState(false);

  const controllerRef = useRef<AbortController | null>(null);
  const stoppedByUserRef = useRef(false);

  const patchTurn = useCallback((id: string, patch: Partial<VideoTurn>) => {
    setTurns((current) =>
      current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)),
    );
  }, []);

  /**
   * The conversation this question belongs to, created on demand.
   *
   * The question is sent as the title so the thread is named from the moment
   * it exists. The server names it from the first turn as well, but only a
   * turn that lands can do that — a first question that fails would otherwise
   * leave an empty thread called "New conversation" in the sidebar forever.
   */
  const ensureConversation = useCallback(
    async (question: string) => {
      if (conversationId) return conversationId;
      const created = await apiFetch<VideoConversationSummary>(
        `/videos/${videoId}/conversations`,
        { method: "POST", body: JSON.stringify({ title: question }) },
      );
      setConversationId(created.conversation_id);
      return created.conversation_id;
    },
    [conversationId, videoId],
  );

  const send = useCallback(
    async (question: string) => {
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
      let streamedText = "";

      try {
        const target = await ensureConversation(submitted);
        const token = await accessToken();
        const response = await trackedFetch(
          `${API_BASE}/video-conversations/${target}/turns/stream`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({ question: submitted }),
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) {
          const raw = await response.text();
          let parsed: string | null = null;
          try {
            parsed = raw
              ? ((JSON.parse(raw) as { detail?: string }).detail ?? null)
              : null;
          } catch {
            parsed = null;
          }
          throw new Error(
            parsed ?? `The video request failed (${response.status}).`,
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
              streamedText += (JSON.parse(payload) as { text: string }).text;
              patchTurn(id, { answer: streamedText });
            } else if (event === "final") {
              const data = JSON.parse(payload) as VideoAskResponse;
              settled = true;
              track("study_answer_completed", { flow: "video_study" });
              setConversationId(data.conversation_id);
              patchTurn(id, {
                answer: data.result.answer,
                result: data.result,
                status: "complete",
                // Recorded server-side under this index, which is what a side
                // chat anchors to. Null for a turn the server did not record.
                turnIndex: data.turn_index ?? undefined,
              });
              break readLoop;
            } else if (event === "error") {
              const data = JSON.parse(payload) as { detail?: string };
              throw new Error(data.detail ?? "The video request failed.");
            }
          }
        }
        if (!settled) {
          throw new Error("The video API closed the stream unexpectedly.");
        }
      } catch (caught) {
        track("study_answer_failed", { flow: "video_study", outcome: stoppedByUserRef.current ? "cancelled" : "failed" });
        const error = caught as Error;
        if (stoppedByUserRef.current) {
          patchTurn(id, { answer: streamedText, status: "stopped" });
        } else {
          patchTurn(id, {
            status: "failed",
            error:
              error.name === "AbortError"
                ? "The video API stopped responding and the request timed out."
                : (error.message ?? "Could not reach the video API."),
          });
        }
      } finally {
        clearTimeout(idleTimer);
        controllerRef.current = null;
        stoppedByUserRef.current = false;
        setIsStreaming(false);
      }
    },
    [ensureConversation, patchTurn],
  );

  const stop = useCallback(() => {
    if (!controllerRef.current) return;
    stoppedByUserRef.current = true;
    controllerRef.current.abort();
  }, []);

  /**
   * Ask the last question again, replacing its turn.
   *
   * The failed or unsatisfying turn is dropped from the list first so the same
   * question does not appear twice. A turn that reached the server was already
   * recorded there, so the regenerated answer becomes a second stored turn —
   * the conversation history is a log of what was asked, not of what is
   * currently on screen.
   */
  const retry = useCallback(async () => {
    const last = turns.at(-1);
    if (!last || isStreaming) return;
    setTurns((current) => current.filter((turn) => turn.id !== last.id));
    await send(last.question);
  }, [turns, isStreaming, send]);

  const reset = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setTurns([]);
    setConversationId(null);
    setIsStreaming(false);
  }, []);

  const resume = useCallback((detail: VideoConversationDetail) => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setIsStreaming(false);
    setConversationId(detail.conversation_id);
    setTurns(
      detail.turns.map((turn) => ({
        id: `${detail.conversation_id}-${turn.turn_index}`,
        question: turn.question,
        answer: turn.answer ?? "",
        status: "complete" as const,
        result: turn.result,
        error: null,
        turnIndex: turn.turn_index,
      })),
    );
  }, []);

  return { turns, conversationId, isStreaming, send, stop, retry, reset, resume };
}
