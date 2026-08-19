"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

export interface WatchPosition {
  timestamp_ms: number;
}

export interface WatchSession {
  conversation_id: string;
  video_id: string;
  title: string;
  position: WatchPosition | null;
  question_count: number;
  updated_at: string;
}

/**
 * How long the playhead has to sit still before the position is recorded.
 *
 * Longer than the reading side's, and for a stronger reason: the playhead
 * reports several times a second while a lecture plays, so writing what it
 * says would be a request per report. What is worth remembering is where the
 * viewer *stopped*, which is exactly what a quiet interval identifies.
 */
const SETTLE_MS = 5_000;

/** Below this, two positions are the same moment. */
const MEANINGFUL_MOVE_MS = 2_000;

/**
 * The viewer's session for one lecture: opened or resumed, and kept in step
 * with the playhead.
 */
export function useWatchSession(videoId: string | null) {
  const [session, setSession] = useState<WatchSession | null>(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(videoId !== null);

  useEffect(() => {
    if (!videoId) {
      setSession(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError("");
    apiFetch<WatchSession>("/watch-sessions", {
      method: "POST",
      body: JSON.stringify({ video_id: videoId }),
    })
      .then((opened) => {
        if (!cancelled) setSession(opened);
      })
      .catch((caught) => {
        if (!cancelled) setError((caught as Error).message);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  const conversationId = session?.conversation_id ?? null;
  const timerRef = useRef<number | null>(null);
  const lastRecorded = useRef<number | null>(null);

  /**
   * Note where the playhead is, once it has stopped moving.
   *
   * Fire-and-forget: a failed write means the next visit opens a few seconds
   * out, which is not worth an error message over the picture.
   */
  const noteMoment = useCallback(
    (timestampMs: number) => {
      if (!conversationId || timestampMs < 0) return;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        const previous = lastRecorded.current;
        if (
          previous !== null &&
          Math.abs(previous - timestampMs) < MEANINGFUL_MOVE_MS
        ) {
          return;
        }
        lastRecorded.current = timestampMs;
        void apiFetch<WatchSession>(
          `/watch-sessions/${conversationId}/position`,
          {
            method: "PATCH",
            body: JSON.stringify({ position: { timestamp_ms: timestampMs } }),
          },
        )
          .then((updated) => setSession(updated))
          .catch(() => undefined);
      }, SETTLE_MS);
    },
    [conversationId],
  );

  useEffect(
    () => () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    },
    [],
  );

  return { session, isLoading, error, noteMoment };
}
