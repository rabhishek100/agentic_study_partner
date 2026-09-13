"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import { passagePath } from "@/lib/passage";
import type { PassageResponse, PassageSegment, ReadingRef } from "@/lib/types";

type Status = "loading" | "ready" | "extending" | "failed";

export interface PassageState {
  segments: PassageSegment[];
  status: Status;
  error: string | null;
  /** Whether there is more of the passage to continue to. */
  hasMore: boolean;
  loadMore: () => void;
  retry: () => void;
}

/**
 * Read one scope's canonical text, an installment at a time.
 *
 * The whole chapter is available; it arrives the way a reader consumes it.
 * Fetching all of it at once would put 90KB of prose into one message and,
 * on a phone, into one layout pass — see `docs/chat-reader-spec.md`.
 *
 * The server decides where installments end, at whole segments, so an offset
 * this hook sends back always names a boundary the server gave it.
 */
export function usePassage(reading: ReadingRef | null | undefined): PassageState {
  const [segments, setSegments] = useState<PassageSegment[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(0);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);

  // Guards a second request while one is in flight. A ref rather than state
  // because two clicks in the same tick both read the same stale state and
  // both fire, which appended one installment twice.
  const pending = useRef(false);
  const key = reading
    ? `${reading.book_id}:${reading.node_id ?? "whole"}`
    : null;

  const fetchFrom = useCallback(
    async (offset: number, mode: "replace" | "append") => {
      if (!reading || pending.current) return;
      pending.current = true;
      setStatus(mode === "replace" ? "loading" : "extending");
      setError(null);
      try {
        const page = await apiFetch<PassageResponse>(
          passagePath(reading, offset),
        );
        setSegments((current) =>
          mode === "replace" ? page.segments : [...current, ...page.segments],
        );
        setNextOffset(page.next_offset);
        setStatus("ready");
      } catch (cause) {
        setError(
          cause instanceof Error
            ? cause.message
            : "The passage could not be loaded.",
        );
        setStatus("failed");
      } finally {
        pending.current = false;
      }
    },
    [reading],
  );

  useEffect(() => {
    if (!key) return;
    // A different scope is a different passage, not more of this one.
    setSegments([]);
    setNextOffset(0);
    void fetchFrom(0, "replace");
    // `fetchFrom` closes over the reading object, which is a fresh literal on
    // every render of a parent; the scope key is what actually changed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return {
    segments,
    status,
    error,
    hasMore: nextOffset !== null,
    loadMore: () => {
      if (nextOffset !== null) void fetchFrom(nextOffset, "append");
    },
    // Retrying resumes where the passage stopped rather than starting over:
    // a failed "continue" already has everything before it on screen.
    retry: () =>
      segments.length === 0
        ? void fetchFrom(0, "replace")
        : void fetchFrom(nextOffset ?? segments.length, "append"),
  };
}
