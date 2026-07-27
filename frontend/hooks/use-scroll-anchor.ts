"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** How close to the bottom still counts as "following the conversation". */
const PIN_THRESHOLD_PX = 64;

/**
 * Keeps a scroll container pinned to the bottom while content streams in, and
 * yields the moment the reader scrolls away.
 *
 * Without this the message list never follows generation at all. With naive
 * auto-scroll it fights the reader, which is worse — so growth only scrolls
 * while the reader is already at the bottom.
 */
export function useScrollAnchor<
  TViewport extends HTMLElement,
  TContent extends HTMLElement,
>() {
  const viewportRef = useRef<TViewport | null>(null);
  const contentRef = useRef<TContent | null>(null);
  const [isPinned, setIsPinned] = useState(true);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior });
    setIsPinned(true);
  }, []);

  // Track whether the reader is still at the bottom.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const onScroll = () => {
      const distance =
        viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      setIsPinned(distance <= PIN_THRESHOLD_PX);
    };

    onScroll();
    viewport.addEventListener("scroll", onScroll, { passive: true });
    return () => viewport.removeEventListener("scroll", onScroll);
  }, []);

  // Follow content growth, but only while pinned.
  useEffect(() => {
    const content = contentRef.current;
    const viewport = viewportRef.current;
    if (!content || !viewport) return;

    const observer = new ResizeObserver(() => {
      if (!isPinned) return;
      // `auto` rather than `smooth`: token-by-token growth with smooth
      // scrolling queues animations and visibly lags behind the text.
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "auto" });
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, [isPinned]);

  return { viewportRef, contentRef, isPinned, scrollToBottom };
}
