"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

/**
 * Trims a list down to what actually fits its frame, so a panel can size its
 * content to the window instead of growing a scrollbar.
 *
 * Three refs, because free space cannot be read off a single box: `frameRef` is
 * the element that owns the available height, `contentRef` wraps everything
 * competing for it (`scrollHeight` would report the frame's own height once the
 * content fits, so it can never say how much room is left over), and `itemsRef`
 * is the flex column whose children may be dropped.
 *
 * Shrinking uses the measured height of the items on screen. Growing is
 * deliberately more timid — it only adds one back when the *tallest* item on
 * screen would still fit, because an optimistic estimate would overflow on the
 * next frame and leave the count flickering between two values.
 *
 * The count can reach zero, which is the point: keeping one item that does not
 * fit just moves the clipping onto that item. Callers should keep the items
 * element mounted while empty, or nothing is left to measure a way back from.
 */
export function useFittedCount(total: number, minimum = 0) {
  const frameRef = useRef<HTMLDivElement | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const itemsRef = useRef<HTMLDivElement | null>(null);
  /** Last known item height, so an emptied list can still be grown back. */
  const tallestRef = useRef(0);
  /**
   * The smallest count already known not to fit, for this frame height and this
   * many items.
   *
   * Growing estimates from the tallest item *on screen*, which says nothing
   * about the height of the one about to come back. When the next item is
   * taller than that estimate, the pass that adds it overflows, the pass after
   * drops it, and the two alternate forever — React stops the page with
   * "Maximum update depth exceeded" rather than the count settling. Remembering
   * what overflowed turns that into a bound: never grow back to a count this
   * frame has already rejected.
   */
  const ceilingRef = useRef<{ available: number; total: number; count: number } | null>(
    null,
  );
  const [count, setCount] = useState(total);

  const measure = useCallback(() => {
    const frame = frameRef.current;
    const content = contentRef.current;
    const items = itemsRef.current;
    if (!frame || !content || !items) return;

    const framePadding = getComputedStyle(frame);
    const available =
      frame.clientHeight -
      (Number.parseFloat(framePadding.paddingTop) || 0) -
      (Number.parseFloat(framePadding.paddingBottom) || 0);
    const free = available - content.offsetHeight;

    const gap = Number.parseFloat(getComputedStyle(items).rowGap) || 0;
    const heights = (Array.from(items.children) as HTMLElement[]).map(
      (child) => child.offsetHeight + gap
    );
    if (heights.length > 0) {
      tallestRef.current = Math.max(...heights);
    }

    // A different frame height or a different list is a different question, so
    // what overflowed under the old one says nothing about this one.
    const ceiling = ceilingRef.current;
    if (ceiling && (ceiling.available !== available || ceiling.total !== total)) {
      ceilingRef.current = null;
    }

    setCount((current) => {
      const shown = Math.min(current, total);
      if (free < 0) {
        ceilingRef.current = { available, total, count: shown };
        let dropped = 0;
        let freed = 0;
        while (freed < -free && shown - dropped > minimum) {
          freed += heights[heights.length - 1 - dropped] ?? 0;
          dropped += 1;
        }
        return shown - dropped;
      }
      const tallest = tallestRef.current;
      if (tallest <= 0) return shown;
      const rejected = ceilingRef.current;
      const limit =
        rejected && rejected.available === available && rejected.total === total
          ? Math.max(minimum, rejected.count - 1)
          : total;
      return Math.min(total, limit, shown + Math.floor(free / tallest));
    });
  }, [minimum, total]);

  // Deliberately runs after every render: each pass either settles or moves the
  // count one step, which is how it converges.
  useLayoutEffect(measure);

  useEffect(() => {
    const frame = frameRef.current;
    const content = contentRef.current;
    if (!frame || !content) return;

    let pending = 0;
    const observer = new ResizeObserver(() => {
      // Re-measuring inside the callback would resize during the same
      // notification cycle; defer a frame so the browser settles first.
      cancelAnimationFrame(pending);
      pending = requestAnimationFrame(measure);
    });
    observer.observe(frame);
    observer.observe(content);
    return () => {
      cancelAnimationFrame(pending);
      observer.disconnect();
    };
  }, [measure]);

  return { frameRef, contentRef, itemsRef, count: Math.min(count, total) };
}
