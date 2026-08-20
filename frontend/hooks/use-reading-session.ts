"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { ReadingSession } from "@/lib/types";

/**
 * How long to sit on a page before recording it.
 *
 * A reader flicking through ten pages to find a diagram is not reading ten
 * pages, and writing each one costs a request that says nothing true. Two
 * seconds is long enough to mean "stopped here" and short enough that closing
 * the tab shortly after settling still records the right page.
 */
const SETTLE_MS = 2_000;

/**
 * The reader's session for one source: opened or resumed, and kept in step
 * with where they are.
 *
 * Resuming rather than creating is the point of the endpoint, so this hook has
 * no "create" of its own — asking for a book twice is asking for the same
 * session, and the questions in its margins come with it.
 */
export function useReadingSession(bookId: number | null) {
  const [session, setSession] = useState<ReadingSession | null>(null);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(bookId !== null);

  useEffect(() => {
    if (bookId === null) {
      setSession(null);
      setIsLoading(false);
      return;
    }
    let cancelled = false;
    setIsLoading(true);
    setError("");
    apiFetch<ReadingSession>("/reading-sessions", {
      method: "POST",
      body: JSON.stringify({ book_id: bookId }),
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
  }, [bookId]);

  const conversationId = session?.conversation_id ?? null;
  const timerRef = useRef<number | null>(null);
  const lastRecorded = useRef<number | null>(null);

  /**
   * Note the page the reader is on, once they have settled on it.
   *
   * Deliberately fire-and-forget: a failed write means the next visit opens a
   * page or two out, which is not worth an error message over the document.
   * The server does not touch `updated_at` for this, so a recorded page cannot
   * reorder the reader's history either.
   */
  const notePage = useCallback(
    (page: number) => {
      if (!conversationId || page < 1) return;
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => {
        if (lastRecorded.current === page) return;
        lastRecorded.current = page;
        void apiFetch<ReadingSession>(
          `/reading-sessions/${conversationId}/position`,
          { method: "PATCH", body: JSON.stringify({ position: { page } }) },
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

  return { session, isLoading, error, notePage };
}
