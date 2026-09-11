"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { API_BASE, apiFetch } from "@/lib/api";
import { drainSseEvents } from "@/lib/sse";
import { accessToken } from "@/lib/supabase";
import { sideChatQueue } from "@/lib/turn-queue";
import type { SideChatSurface, SideChatTurn, StoredSideChatTurn } from "@/lib/side-chat";
import type { ResponseDepth } from "@/lib/types";

/** How long the stream may go quiet before the client gives up on it. */
const STREAM_IDLE_TIMEOUT_MS = 60_000;

/**
 * Read a failure body into something a reader can act on.
 *
 * FastAPI reports a rejected request with `detail` as a *list* of field errors,
 * not a string. Reading it as a string put "[object Object]" in the window,
 * which named neither the field nor the reason — so the actual cause (a request
 * carrying a field the endpoint forbids) was invisible.
 */
export function describeFailure(body: string): string | null {
  if (!body.trim()) return null;
  let detail: unknown;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail;
  } catch {
    return null;
  }
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const described = detail
      .map((entry) => {
        if (typeof entry === "string") return entry;
        const item = entry as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(item.loc)
          ? item.loc.filter((part) => part !== "body").join(".")
          : "";
        return [field, item.msg].filter(Boolean).join(": ");
      })
      .filter(Boolean);
    return described.length ? described.join("; ") : null;
  }
  return detail ? JSON.stringify(detail) : null;
}

/**
 * Owns one side chat's turns and the stream that fills them.
 *
 * Deliberately the same shape as the book and video chat hooks: turns
 * addressed by a stable client id, the server owning conversation state, one
 * `final` event settling the turn. Two things differ. The conversation already
 * exists — it was created when the reader anchored it, so there is nothing to
 * create on first send — and its turns are loaded on mount, because a side
 * chat that was closed and reopened must come back with its history rather
 * than as an empty window.
 */
export function useSideChat<TResult>(
  sideChatId: string,
  surface: SideChatSurface,
  /**
   * The **stay in this source** lock, sent with every turn this window asks.
   *
   * Read from a ref at send time rather than captured, so toggling it applies
   * to the next question rather than to whichever render the composer was
   * built in.
   */
  stayInSource = false,
) {
  const [turns, setTurns] = useState<SideChatTurn<TResult>[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  // Sent, but waiting for one of the shared generation slots.
  const [isQueued, setIsQueued] = useState(false);

  const controllerRef = useRef<AbortController | null>(null);
  const stoppedByUserRef = useRef(false);
  const stayInSourceRef = useRef(stayInSource);
  stayInSourceRef.current = stayInSource;

  const patchTurn = useCallback(
    (id: string, patch: Partial<SideChatTurn<TResult>>) => {
      setTurns((current) =>
        current.map((turn) => (turn.id === id ? { ...turn, ...patch } : turn)),
      );
    },
    [],
  );

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    apiFetch<{
      conversation_id: string;
      turns: StoredSideChatTurn<TResult>[];
    }>(surface.detail(sideChatId))
      .then((detail) => {
        if (cancelled) return;
        setTurns(
          detail.turns
            // A lecture turn can be recorded as running or failed, with no
            // answer to show; the book endpoint only ever returns settled ones.
            .filter((turn) => turn.answer)
            .map((turn) => ({
              id: `${detail.conversation_id}-${turn.turn_index}`,
              question: turn.question,
              answer: turn.answer ?? "",
              status: "complete" as const,
              result: turn.result,
              error: null,
              turnIndex: turn.turn_index,
            })),
        );
      })
      .catch(() => {
        // An unreachable history is not worth an error state in a small
        // window: the reader can still ask. A failed *turn* does report.
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sideChatId, surface]);

  const send = useCallback(
    async (question: string, responseDepth: ResponseDepth) => {
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

      // Wait for a generation slot before opening the request, so several
      // windows asking at once queue instead of firing together. The idle
      // timeout starts after the slot is held: time spent queued is not the
      // API failing to respond.
      setIsQueued(true);
      const release = await sideChatQueue.acquire(sideChatId);
      setIsQueued(false);
      if (stoppedByUserRef.current) {
        // Stopped while queued: the turn never reached the server.
        release();
        patchTurn(id, { status: "stopped" });
        controllerRef.current = null;
        stoppedByUserRef.current = false;
        setIsStreaming(false);
        return;
      }

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
        const token = await accessToken();
        const response = await fetch(
          `${API_BASE}${surface.stream(sideChatId)}`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: JSON.stringify(
              surface.supportsDepth
                ? {
                    question: submitted,
                    response_depth: responseDepth,
                    stay_in_source: stayInSourceRef.current,
                  }
                : // The lecture surface has no route out of the recording, so
                  // there is nothing there for a lock to stop, and its turn
                  // contract forbids unknown fields.
                  { question: submitted },
            ),
            signal: controller.signal,
          },
        );
        if (!response.ok || !response.body) {
          const raw = await response.text();
          throw new Error(
            describeFailure(raw) ??
              `The side chat request failed (${response.status}).`,
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
              const data = JSON.parse(payload) as {
                result: TResult & { answer: string };
                turn_index: number;
              };
              settled = true;
              patchTurn(id, {
                answer: data.result.answer,
                result: data.result,
                status: "complete",
                turnIndex: data.turn_index,
              });
              break readLoop;
            } else if (event === "error") {
              throw new Error(
                describeFailure(payload) ?? "The side chat request failed.",
              );
            }
          }
        }
        if (!settled) {
          throw new Error("The study API closed the stream unexpectedly.");
        }
      } catch (caught) {
        const error = caught as Error;
        if (stoppedByUserRef.current) {
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
        release();
        controllerRef.current = null;
        stoppedByUserRef.current = false;
        setIsStreaming(false);
      }
    },
    [patchTurn, sideChatId, surface],
  );

  const stop = useCallback(() => {
    if (!controllerRef.current) return;
    stoppedByUserRef.current = true;
    // A turn still queued has no request to abort; dropping it from the queue
    // is what stopping means at that point.
    sideChatQueue.cancel(sideChatId);
    controllerRef.current.abort();
  }, [sideChatId]);

  /** Abort an in-flight turn when the window goes away for good. */
  useEffect(
    () => () => {
      sideChatQueue.cancel(sideChatId);
      controllerRef.current?.abort();
      controllerRef.current = null;
    },
    [sideChatId],
  );

  return { turns, isStreaming, isQueued, isLoading, send, stop };
}
