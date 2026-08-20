"use client";

import { useCallback, useRef, useState } from "react";

import type { PageSelection } from "@/components/read/page-selection";

/** A drag shorter than this is a click that missed, not a region. */
const MINIMUM_SIDE_PX = 12;

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

function normalise(fromX: number, fromY: number, toX: number, toY: number): Rect {
  return {
    left: Math.min(fromX, toX),
    top: Math.min(fromY, toY),
    width: Math.abs(toX - fromX),
    height: Math.abs(toY - fromY),
  };
}

/**
 * The words a drawn rectangle covers, in reading order.
 *
 * The rectangle is a *selection tool*, not a new kind of anchor: it produces
 * exactly what dragging across the text would, and the anchor that results is
 * the same `document_passage`. What it buys is the cases where dragging is
 * hopeless — a column of a table, one cell, a block of a two-column layout —
 * where a text drag runs across the page and picks up the neighbouring column.
 *
 * A span counts when its centre is inside the rectangle rather than when it
 * merely touches: catching every span the rectangle grazes at the edges is how
 * a tight selection ends up carrying half the paragraph beside it.
 */
export function textWithin(container: HTMLElement, rect: Rect): string {
  const bounds = container.getBoundingClientRect();
  const spans = Array.from(
    container.querySelectorAll<HTMLElement>(".react-pdf__Page__textContent span"),
  );
  const covered = spans.filter((span) => {
    const box = span.getBoundingClientRect();
    const centreX = box.left + box.width / 2 - bounds.left;
    const centreY = box.top + box.height / 2 - bounds.top;
    return (
      centreX >= rect.left &&
      centreX <= rect.left + rect.width &&
      centreY >= rect.top &&
      centreY <= rect.top + rect.height
    );
  });
  return covered
    .map((span) => span.textContent ?? "")
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

export interface RegionSelectProps {
  /** The element the rectangle is drawn over, and whose spans are read. */
  container: React.RefObject<HTMLElement | null>;
  active: boolean;
  /** Called with the words inside the rectangle, and where to put the offer. */
  onRegion: (selection: PageSelection) => void;
  /** Called when a drag produced nothing to ask about. */
  onEmpty?: () => void;
}

/**
 * Draw a rectangle over the page to choose what to ask about.
 *
 * Only mounted while armed, so ordinary reading — scrolling, selecting text,
 * following a citation — is untouched by it. Escape disarms.
 */
export function RegionSelect({
  container,
  active,
  onRegion,
  onEmpty,
}: RegionSelectProps) {
  const [rect, setRect] = useState<Rect | null>(null);
  const originRef = useRef<{ x: number; y: number } | null>(null);

  const positionOf = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const bounds = event.currentTarget.getBoundingClientRect();
      return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
    },
    [],
  );

  if (!active) return null;

  return (
    <div
      // Above the page and below the popover it produces.
      className="absolute inset-0 z-docked cursor-crosshair"
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.currentTarget.setPointerCapture(event.pointerId);
        const start = positionOf(event);
        originRef.current = start;
        setRect({ left: start.x, top: start.y, width: 0, height: 0 });
      }}
      onPointerMove={(event) => {
        const origin = originRef.current;
        if (!origin) return;
        const current = positionOf(event);
        setRect(normalise(origin.x, origin.y, current.x, current.y));
      }}
      onPointerUp={(event) => {
        const origin = originRef.current;
        originRef.current = null;
        const drawn = rect;
        setRect(null);
        const root = container.current;
        if (!origin || !drawn || !root) return;
        if (drawn.width < MINIMUM_SIDE_PX || drawn.height < MINIMUM_SIDE_PX) {
          onEmpty?.();
          return;
        }
        const text = textWithin(root, drawn);
        if (!text) {
          // A rectangle over a figure covers no text layer at all. Saying so
          // beats opening an offer to ask about nothing.
          onEmpty?.();
          return;
        }
        const box = event.currentTarget.getBoundingClientRect();
        onRegion({
          text,
          x: box.left + drawn.left + drawn.width / 2,
          y: box.top + drawn.top - 10,
        });
      }}
    >
      {rect && (
        <div
          aria-hidden
          // Outline only. A fill would be a derived colour the token contract
          // forbids, and over a rendered page it would hide the thing being
          // selected — which is the one thing the reader is looking at.
          className="absolute border-2 border-primary"
          style={{
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
          }}
        />
      )}
    </div>
  );
}
