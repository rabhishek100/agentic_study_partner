"use client";

import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { ReadingSession } from "@/lib/types";
import type { WatchSession } from "@/hooks/use-watch-session";

/**
 * The sources this reader has started, for the library's continue band.
 *
 * Separate calls per surface rather than one combined list, because the
 * library keeps books, papers and lectures as distinct areas and a band that
 * mixed them would be the first thing to cross that line.
 */
export function useReadingSessions(enabled: boolean) {
  const [sessions, setSessions] = useState<ReadingSession[]>([]);
  const reload = useCallback(() => {
    if (!enabled) return;
    apiFetch<{ sessions: ReadingSession[] }>("/reading-sessions")
      .then((payload) => setSessions(payload.sessions))
      // A band that fails to load is one fewer shortcut, not a broken library.
      .catch(() => undefined);
  }, [enabled]);

  useEffect(reload, [reload]);
  return { sessions, reload };
}

export function useWatchSessions(enabled: boolean) {
  const [sessions, setSessions] = useState<WatchSession[]>([]);
  const reload = useCallback(() => {
    if (!enabled) return;
    apiFetch<{ sessions: WatchSession[] }>("/watch-sessions")
      .then((payload) => setSessions(payload.sessions))
      .catch(() => undefined);
  }, [enabled]);

  useEffect(reload, [reload]);
  return { sessions, reload };
}
