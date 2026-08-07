/**
 * Geometry for the floating side-chat windows.
 *
 * Kept as pure functions over plain rectangles, separately from the component
 * that renders them, because this is where the bugs in a window manager live:
 * a window stranded off-screen after the viewport shrinks, a remembered size
 * smaller than its own header, two windows opening exactly on top of each
 * other. Each of those is a property of a rectangle and a viewport, so each is
 * testable without a DOM.
 *
 * Coordinates are viewport pixels, matching `position: fixed`.
 */

export interface WindowRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export const MIN_WIDTH = 288;
export const MIN_HEIGHT = 220;
export const DEFAULT_WIDTH = 384;
export const DEFAULT_HEIGHT = 460;

/** How close to an edge counts as meaning "against the edge". */
export const SNAP_THRESHOLD = 16;

/** One arrow-key nudge, and one with Shift held. */
export const KEYBOARD_STEP = 16;
export const KEYBOARD_LARGE_STEP = 64;

/** Kept on screen: a window dragged fully out of view cannot be dragged back. */
const MINIMUM_VISIBLE = 96;

/**
 * The app header's height, which windows stay clear of.
 *
 * Found by opening one: a window placed at the top of the viewport covered the
 * account menu and the books/videos navigation. The reader has to be able to
 * reach the application while a side chat is open, so the top of the viewport
 * is not available to windows.
 */
export const HEADER_INSET = 56;

/** Below this width the floating layer is replaced by a docked sheet. */
export const FLOATING_MIN_VIEWPORT_WIDTH = 1024;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/**
 * Fit a rectangle inside a viewport.
 *
 * The size is reduced before the position is moved, so a window larger than
 * the viewport ends up fully visible rather than pinned at the top-left with
 * its bottom half off-screen. `MINIMUM_VISIBLE` then guarantees a grabbable
 * strip of header remains reachable even at the edges.
 */
export function clampRect(rect: WindowRect, viewport: Viewport): WindowRect {
  const width = clamp(
    Math.round(rect.width),
    MIN_WIDTH,
    Math.max(MIN_WIDTH, viewport.width),
  );
  const height = clamp(
    Math.round(rect.height),
    MIN_HEIGHT,
    Math.max(MIN_HEIGHT, viewport.height - HEADER_INSET),
  );
  return {
    width,
    height,
    x: clamp(
      Math.round(rect.x),
      MINIMUM_VISIBLE - width,
      Math.max(0, viewport.width - MINIMUM_VISIBLE),
    ),
    // Never under the app header, and never so low that the window's own
    // header — its only move and close controls — leaves the viewport.
    y: clamp(
      Math.round(rect.y),
      HEADER_INSET,
      Math.max(HEADER_INSET, viewport.height - MINIMUM_VISIBLE),
    ),
  };
}

/**
 * Pull a nearly-aligned window onto the viewport edge it is nearly on.
 *
 * Deliberately soft: it only fires within `SNAP_THRESHOLD`, so a window placed
 * deliberately mid-screen stays where it was put. Snapping is what keeps a
 * screen of windows looking arranged rather than scattered.
 */
export function snapRect(rect: WindowRect, viewport: Viewport): WindowRect {
  const right = viewport.width - (rect.x + rect.width);
  const bottom = viewport.height - (rect.y + rect.height);
  let { x, y } = rect;
  if (Math.abs(rect.x) <= SNAP_THRESHOLD) x = 0;
  else if (Math.abs(right) <= SNAP_THRESHOLD) x = viewport.width - rect.width;
  // The top edge a window snaps to is the bottom of the app header, not the
  // top of the viewport, for the same reason `clampRect` stops there.
  if (Math.abs(rect.y - HEADER_INSET) <= SNAP_THRESHOLD) y = HEADER_INSET;
  else if (Math.abs(bottom) <= SNAP_THRESHOLD) y = viewport.height - rect.height;
  return { ...rect, x, y };
}

/**
 * Where the next window opens.
 *
 * Windows cascade down-left from the right edge so a new one never lands
 * exactly on the last one — two identical stacked windows read as one window
 * that failed to open. The cascade restarts once it would leave the viewport.
 */
export function cascadePlacement(
  index: number,
  viewport: Viewport,
  size: { width: number; height: number } = {
    width: DEFAULT_WIDTH,
    height: DEFAULT_HEIGHT,
  },
): WindowRect {
  const offset = 28;
  const margin = 16;
  const top = HEADER_INSET + margin;
  const steps = Math.max(
    1,
    Math.floor((viewport.height - size.height - top) / offset) || 1,
  );
  const step = index % steps;
  return clampRect(
    {
      x: viewport.width - size.width - 24 - step * offset,
      y: top + step * offset,
      width: size.width,
      height: size.height,
    },
    viewport,
  );
}

export type MoveKey =
  | "ArrowUp"
  | "ArrowDown"
  | "ArrowLeft"
  | "ArrowRight";

const DELTAS: Record<MoveKey, { x: number; y: number }> = {
  ArrowUp: { x: 0, y: -1 },
  ArrowDown: { x: 0, y: 1 },
  ArrowLeft: { x: -1, y: 0 },
  ArrowRight: { x: 1, y: 0 },
};

export function isMoveKey(key: string): key is MoveKey {
  return key in DELTAS;
}

/** Move a window by one keyboard step, clamped to the viewport. */
export function moveByKey(
  rect: WindowRect,
  key: MoveKey,
  viewport: Viewport,
  { large = false }: { large?: boolean } = {},
): WindowRect {
  const delta = DELTAS[key];
  const step = large ? KEYBOARD_LARGE_STEP : KEYBOARD_STEP;
  return clampRect(
    { ...rect, x: rect.x + delta.x * step, y: rect.y + delta.y * step },
    viewport,
  );
}

/** Resize a window by one keyboard step, clamped to the viewport. */
export function resizeByKey(
  rect: WindowRect,
  key: MoveKey,
  viewport: Viewport,
  { large = false }: { large?: boolean } = {},
): WindowRect {
  const delta = DELTAS[key];
  const step = large ? KEYBOARD_LARGE_STEP : KEYBOARD_STEP;
  return clampRect(
    {
      ...rect,
      width: rect.width + delta.x * step,
      height: rect.height + delta.y * step,
    },
    viewport,
  );
}

const GEOMETRY_KEY_PREFIX = "side-chat:geometry:";

/** Read one window's remembered geometry, ignoring anything unusable. */
export function readGeometry(sideChatId: string): WindowRect | null {
  try {
    const raw = window.localStorage.getItem(
      `${GEOMETRY_KEY_PREFIX}${sideChatId}`,
    );
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WindowRect>;
    const values = [parsed.x, parsed.y, parsed.width, parsed.height];
    if (!values.every((value) => typeof value === "number" && Number.isFinite(value))) {
      return null;
    }
    return parsed as WindowRect;
  } catch {
    // A geometry that cannot be parsed is not worth reporting: the window
    // simply opens at its default position.
    return null;
  }
}

export function writeGeometry(sideChatId: string, rect: WindowRect): void {
  try {
    window.localStorage.setItem(
      `${GEOMETRY_KEY_PREFIX}${sideChatId}`,
      JSON.stringify(rect),
    );
  } catch {
    // Storage can be full or blocked; losing a remembered position is not a
    // reason to fail the interaction that moved the window.
  }
}

export function forgetGeometry(sideChatId: string): void {
  try {
    window.localStorage.removeItem(`${GEOMETRY_KEY_PREFIX}${sideChatId}`);
  } catch {
    // As above.
  }
}
