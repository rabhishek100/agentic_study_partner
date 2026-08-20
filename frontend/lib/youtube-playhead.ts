/**
 * Reading the playhead out of a YouTube embed.
 *
 * The embed sends nothing to a page that has not asked. With `enablejsapi=1`
 * it accepts a `listening` handshake over `postMessage` and then reports its
 * state back as `infoDelivery` messages — which is how the player's time can
 * be read without loading the IFrame Player API script, keeping the component
 * free of an external runtime dependency exactly as its seeking already does.
 *
 * The parsing lives here rather than in the component because every message
 * arriving at the window is untrusted input: anything on the page can post to
 * it, and a frame's own origin is the only thing that says a message came from
 * the player. The caller checks the origin and the source; this decides
 * whether what arrived is a time report at all.
 */

export const YOUTUBE_EMBED_ORIGIN = "https://www.youtube-nocookie.com";

/**
 * What the embed actually sends back, observed against a real one rather than
 * assumed:
 *
 * - a fresh frame answers the handshake with `initialDelivery`, carrying
 *   `currentTime`, `duration` and `playerState` — so a position is available
 *   before anything is played, which is what the ambient chip needs;
 * - `onReady` follows, carrying nothing;
 * - during playback `infoDelivery` streams `currentTime` several times a
 *   second;
 * - a handshake sent to an already-initialised frame is answered only with
 *   `alreadyInitialized`, so repeating it after the first answer produces
 *   nothing but noise. The caller stops once it has been answered.
 */
const TIME_EVENTS = new Set(["infoDelivery", "initialDelivery"]);

/** The handshake that makes the embed start reporting. */
export function listeningMessage(): string {
  return JSON.stringify({ event: "listening", id: 1, channel: "widget" });
}

/**
 * The playhead position a message carries, in milliseconds, or null.
 *
 * Null for everything else the embed says — and it says a great deal — so a
 * caller can treat "not a time report" and "a malformed one" identically,
 * which they are: neither tells you where the video is.
 */
export function playheadFrom(data: unknown): number | null {
  if (typeof data !== "string") return null;
  let payload: unknown;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  if (!payload || typeof payload !== "object") return null;
  const message = payload as { event?: unknown; info?: unknown };
  if (typeof message.event !== "string" || !TIME_EVENTS.has(message.event)) {
    return null;
  }
  const info = message.info as { currentTime?: unknown } | undefined;
  const currentTime = info?.currentTime;
  // Zero is a legitimate position — it is what a cued video reports — so it
  // is admitted rather than treated as missing.
  if (typeof currentTime !== "number" || !Number.isFinite(currentTime)) {
    return null;
  }
  if (currentTime < 0) return null;
  return Math.round(currentTime * 1000);
}

/** Whether a message means the embed has answered and can stop being asked. */
export function isHandshakeAnswered(data: unknown): boolean {
  if (typeof data !== "string") return false;
  try {
    const payload = JSON.parse(data) as { event?: unknown };
    return (
      payload?.event === "onReady" ||
      payload?.event === "initialDelivery" ||
      payload?.event === "alreadyInitialized"
    );
  } catch {
    return false;
  }
}
