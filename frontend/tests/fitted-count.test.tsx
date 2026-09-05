import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useFittedCount } from "@/hooks/use-fitted-count";

/**
 * jsdom lays nothing out, so heights come from `data-h` (an element's own) and
 * `data-ch` (a frame's available height); anything without `data-h` is the sum
 * of its descendants, which is what makes the content wrapper behave like a
 * real one.
 */
const descriptors: [string, PropertyDescriptor | undefined][] = [];

beforeEach(() => {
  for (const name of ["offsetHeight", "clientHeight"] as const) {
    descriptors.push([
      name,
      Object.getOwnPropertyDescriptor(HTMLElement.prototype, name),
    ]);
  }

  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.dataset.h) return Number(this.dataset.h);
      return Array.from(this.children).reduce(
        (total, child) => total + (child as HTMLElement).offsetHeight,
        0,
      );
    },
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get(this: HTMLElement) {
      return this.dataset.ch ? Number(this.dataset.ch) : 0;
    },
  });
});

afterEach(() => {
  for (const [name, descriptor] of descriptors.splice(0)) {
    if (descriptor) Object.defineProperty(HTMLElement.prototype, name, descriptor);
  }
});

function Fitted({ heights, frame }: { heights: number[]; frame: number }) {
  const { frameRef, contentRef, itemsRef, count } = useFittedCount(
    heights.length,
  );

  return (
    <div ref={frameRef} data-ch={frame}>
      <div ref={contentRef}>
        <div ref={itemsRef}>
          {heights.slice(0, count).map((height, index) => (
            <div key={index} data-h={height}>
              item {index + 1}
            </div>
          ))}
        </div>
      </div>
      <p>showing {count}</p>
    </div>
  );
}

describe("useFittedCount", () => {
  it("settles when the item it would grow back is taller than the ones on screen", () => {
    // 30 + 30 + 90 in a 100px frame. Three overflow, so the 90 is dropped; two
    // leave 40px free, and the tallest item *on screen* is 30 — so the estimate
    // says one more fits, the 90 comes back, and it overflows again. The two
    // counts alternated forever, and React stopped the page with "Maximum
    // update depth exceeded" rather than the list settling.
    render(<Fitted heights={[30, 30, 90]} frame={100} />);

    expect(screen.getByText("showing 2")).toBeInTheDocument();
  });

  it("shows everything that fits", () => {
    render(<Fitted heights={[30, 30, 30]} frame={200} />);

    expect(screen.getByText("showing 3")).toBeInTheDocument();
  });

  it("reaches zero rather than keeping an item that does not fit", () => {
    render(<Fitted heights={[80]} frame={20} />);

    expect(screen.getByText("showing 0")).toBeInTheDocument();
  });
});
