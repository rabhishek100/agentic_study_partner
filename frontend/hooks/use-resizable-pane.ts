"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const KEYBOARD_STEP = 5;

export interface ResizablePaneOptions {
  /** Where the remembered width is stored, per surface. */
  storageKey: string;
  /** Which edge of the row the sized pane is docked against. */
  edge: "left" | "right";
  /** What the separator is called to assistive technology. */
  label: string;
  defaultPercent: number;
  minPercent: number;
  maxPercent: number;
  /** Skip the global listeners entirely while the pane is closed. */
  enabled?: boolean;
}

export interface SeparatorProps {
  role: "separator";
  "aria-orientation": "vertical";
  "aria-label": string;
  "aria-valuenow": number;
  "aria-valuemin": number;
  "aria-valuemax": number;
  tabIndex: 0;
  onPointerDown: (event: React.PointerEvent) => void;
  onKeyDown: (event: React.KeyboardEvent) => void;
}

/**
 * One draggable, keyboard-operable, remembered column width.
 *
 * Extracted from the reading pane so the video workspace resizes the same way
 * rather than growing a second implementation of the same three bugs: dragging
 * that outlives the pointer, a stored width outside today's limits, and body
 * styles left behind when the pane unmounts mid-drag.
 *
 * The arrow keys always grow the pane on first press regardless of which edge
 * it is docked to, because "toward the pane" is what the reader means.
 */
export function useResizablePane({
  storageKey,
  edge,
  label,
  defaultPercent,
  minPercent,
  maxPercent,
  enabled = true,
}: ResizablePaneOptions) {
  const [percent, setPercent] = useState(defaultPercent);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const draggingRef = useRef(false);

  const clamp = useCallback(
    (value: number) => Math.min(maxPercent, Math.max(minPercent, value)),
    [minPercent, maxPercent],
  );

  useEffect(() => {
    const stored = Number(window.localStorage.getItem(storageKey));
    if (Number.isFinite(stored) && stored > 0) setPercent(clamp(stored));
  }, [storageKey, clamp]);

  const store = useCallback(
    (value: number) => {
      window.localStorage.setItem(storageKey, String(Math.round(value)));
    },
    [storageKey],
  );

  useEffect(() => {
    if (!enabled) return;

    const onMove = (event: PointerEvent) => {
      if (!draggingRef.current || !containerRef.current) return;
      const bounds = containerRef.current.getBoundingClientRect();
      const span =
        edge === "right"
          ? bounds.right - event.clientX
          : event.clientX - bounds.left;
      setPercent(clamp((span / bounds.width) * 100));
    };
    const onUp = () => {
      if (!draggingRef.current) return;
      draggingRef.current = false;
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
      setPercent((current) => {
        store(current);
        return current;
      });
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      draggingRef.current = false;
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
    };
  }, [enabled, edge, clamp, store]);

  const separatorProps: SeparatorProps = {
    role: "separator",
    "aria-orientation": "vertical",
    "aria-label": label,
    "aria-valuenow": Math.round(percent),
    "aria-valuemin": minPercent,
    "aria-valuemax": maxPercent,
    tabIndex: 0,
    onPointerDown: (event) => {
      event.preventDefault();
      draggingRef.current = true;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    onKeyDown: (event) => {
      const toward = edge === "right" ? "ArrowLeft" : "ArrowRight";
      const away = edge === "right" ? "ArrowRight" : "ArrowLeft";
      const direction =
        event.key === toward ? 1 : event.key === away ? -1 : 0;
      if (!direction) return;
      event.preventDefault();
      setPercent((current) => {
        const next = clamp(current + direction * KEYBOARD_STEP);
        store(next);
        return next;
      });
    },
  };

  return { percent, containerRef, separatorProps };
}
