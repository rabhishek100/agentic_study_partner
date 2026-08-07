import { describe, expect, it } from "vitest";

import {
  DEFAULT_HEIGHT,
  DEFAULT_WIDTH,
  HEADER_INSET,
  MIN_HEIGHT,
  MIN_WIDTH,
  cascadePlacement,
  clampRect,
  forgetGeometry,
  isMoveKey,
  moveByKey,
  readGeometry,
  resizeByKey,
  snapRect,
  writeGeometry,
} from "@/lib/floating-window";

const VIEWPORT = { width: 1280, height: 800 };

const rect = (
  overrides: Partial<{ x: number; y: number; width: number; height: number }> = {},
) => ({ x: 100, y: 100, width: DEFAULT_WIDTH, height: DEFAULT_HEIGHT, ...overrides });

describe("clampRect", () => {
  it("keeps a grabbable strip on screen when dragged off the right edge", () => {
    const clamped = clampRect(rect({ x: 5000 }), VIEWPORT);

    expect(clamped.x).toBeLessThanOrEqual(VIEWPORT.width - 96);
    expect(clamped.x + clamped.width).toBeGreaterThan(0);
  });

  it("keeps a grabbable strip on screen when dragged off the left edge", () => {
    const clamped = clampRect(rect({ x: -5000 }), VIEWPORT);

    expect(clamped.x + clamped.width).toBeGreaterThanOrEqual(96);
  });

  it("keeps a window clear of the app header", () => {
    // The header carries navigation and the account menu; a window covering it
    // would make the application unreachable while a side chat is open.
    expect(clampRect(rect({ y: -400 }), VIEWPORT).y).toBe(HEADER_INSET);
    expect(clampRect(rect({ y: 10 }), VIEWPORT).y).toBe(HEADER_INSET);
  });

  it("shrinks a window larger than the viewport instead of overflowing it", () => {
    const clamped = clampRect(
      rect({ x: 0, y: 0, width: 4000, height: 4000 }),
      VIEWPORT,
    );

    expect(clamped.width).toBe(VIEWPORT.width);
    expect(clamped.height).toBe(VIEWPORT.height - HEADER_INSET);
  });

  it("refuses a size smaller than the window's own chrome", () => {
    const clamped = clampRect(rect({ width: 10, height: 10 }), VIEWPORT);

    expect(clamped.width).toBe(MIN_WIDTH);
    expect(clamped.height).toBe(MIN_HEIGHT);
  });

  it("keeps the title bar reachable in a viewport smaller than the minimum window", () => {
    // Nothing can make a 220px-tall minimum fit 150px of viewport. What must
    // hold is that the window's own title bar — its only move and close
    // controls — is still on screen.
    const clamped = clampRect(rect(), { width: 200, height: 150 });

    expect(clamped.width).toBe(MIN_WIDTH);
    expect(clamped.height).toBe(MIN_HEIGHT);
    expect(clamped.y).toBe(HEADER_INSET);
  });
});

describe("snapRect", () => {
  it("pulls a nearly-flush window onto the left edge and under the header", () => {
    expect(
      snapRect(rect({ x: 9, y: HEADER_INSET + 7 }), VIEWPORT),
    ).toMatchObject({ x: 0, y: HEADER_INSET });
  });

  it("snaps to the right and bottom edges", () => {
    const nearCorner = rect({
      x: VIEWPORT.width - DEFAULT_WIDTH - 6,
      y: VIEWPORT.height - DEFAULT_HEIGHT - 4,
    });

    expect(snapRect(nearCorner, VIEWPORT)).toMatchObject({
      x: VIEWPORT.width - DEFAULT_WIDTH,
      y: VIEWPORT.height - DEFAULT_HEIGHT,
    });
  });

  it("leaves a window placed deliberately mid-screen alone", () => {
    const placed = rect({ x: 400, y: 300 });

    expect(snapRect(placed, VIEWPORT)).toEqual(placed);
  });
});

describe("cascadePlacement", () => {
  it("opens the first window near the top right", () => {
    const first = cascadePlacement(0, VIEWPORT);

    expect(first.x + first.width).toBeLessThanOrEqual(VIEWPORT.width);
    expect(first.y).toBeGreaterThanOrEqual(0);
  });

  it("does not stack two windows on exactly the same spot", () => {
    const first = cascadePlacement(0, VIEWPORT);
    const second = cascadePlacement(1, VIEWPORT);

    expect([second.x, second.y]).not.toEqual([first.x, first.y]);
  });

  it("stays inside a short viewport", () => {
    const placed = cascadePlacement(7, { width: 1100, height: 500 });

    expect(placed.y).toBeGreaterThanOrEqual(0);
    expect(placed.y).toBeLessThan(500);
  });
});

describe("keyboard geometry", () => {
  it("recognises only the arrow keys", () => {
    expect(isMoveKey("ArrowLeft")).toBe(true);
    expect(isMoveKey("Enter")).toBe(false);
  });

  it("moves by one step, and further with shift", () => {
    expect(moveByKey(rect(), "ArrowRight", VIEWPORT).x).toBe(116);
    expect(moveByKey(rect(), "ArrowRight", VIEWPORT, { large: true }).x).toBe(
      164,
    );
  });

  it("clamps a keyboard move at the top of the available area", () => {
    const atEdge = rect({ y: HEADER_INSET });

    expect(moveByKey(atEdge, "ArrowUp", VIEWPORT).y).toBe(HEADER_INSET);
  });

  it("resizes by one step in each direction", () => {
    expect(resizeByKey(rect(), "ArrowRight", VIEWPORT).width).toBe(
      DEFAULT_WIDTH + 16,
    );
    expect(resizeByKey(rect(), "ArrowUp", VIEWPORT).height).toBe(
      DEFAULT_HEIGHT - 16,
    );
  });

  it("cannot resize below the minimum", () => {
    const small = rect({ width: MIN_WIDTH, height: MIN_HEIGHT });

    expect(resizeByKey(small, "ArrowLeft", VIEWPORT).width).toBe(MIN_WIDTH);
  });
});

describe("remembered geometry", () => {
  it("round-trips a rectangle", () => {
    writeGeometry("side-1", rect({ x: 12, y: 34 }));

    expect(readGeometry("side-1")).toMatchObject({ x: 12, y: 34 });
  });

  it("ignores stored values that are not a rectangle", () => {
    window.localStorage.setItem(
      "side-chat:geometry:side-2",
      JSON.stringify({ x: "left", y: null }),
    );

    expect(readGeometry("side-2")).toBeNull();
  });

  it("ignores unparseable storage rather than throwing", () => {
    window.localStorage.setItem("side-chat:geometry:side-3", "{oops");

    expect(readGeometry("side-3")).toBeNull();
  });

  it("returns null for a window it has never seen", () => {
    expect(readGeometry("side-4")).toBeNull();
  });

  it("forgets a deleted side chat's geometry", () => {
    writeGeometry("side-5", rect());
    forgetGeometry("side-5");

    expect(readGeometry("side-5")).toBeNull();
  });
});
