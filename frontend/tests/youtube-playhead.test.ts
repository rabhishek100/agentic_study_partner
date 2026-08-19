import { describe, expect, it } from "vitest";

import {
  isHandshakeAnswered,
  listeningMessage,
  playheadFrom,
} from "@/lib/youtube-playhead";

/**
 * The payloads below are shapes observed from a real youtube-nocookie embed,
 * not invented ones: a fresh frame answers the handshake with
 * `initialDelivery` carrying a position, and playback then streams
 * `infoDelivery`.
 */
const INITIAL = JSON.stringify({
  event: "initialDelivery",
  info: { currentTime: 0, duration: 253, playerState: 5 },
});
const PLAYING = JSON.stringify({
  event: "infoDelivery",
  info: { currentTime: 6.495982, duration: 253, playerState: 1 },
});
const READY = JSON.stringify({ event: "onReady" });
const ALREADY = JSON.stringify({ event: "alreadyInitialized" });

describe("listeningMessage", () => {
  it("asks the embed to start reporting", () => {
    expect(JSON.parse(listeningMessage())).toEqual({
      event: "listening",
      id: 1,
      channel: "widget",
    });
  });
});

describe("playheadFrom", () => {
  it("reads the position out of a playing report", () => {
    expect(playheadFrom(PLAYING)).toBe(6496);
  });

  it("reads a position before anything has played", () => {
    // The ambient chip needs a timestamp the moment the lecture opens, not
    // only once the reader presses play. A cued frame reports zero, and zero
    // is a position rather than a missing one.
    expect(playheadFrom(INITIAL)).toBe(0);
  });

  it("ignores the messages that carry no position", () => {
    expect(playheadFrom(READY)).toBeNull();
    expect(playheadFrom(ALREADY)).toBeNull();
  });

  it("ignores anything that is not the embed's protocol at all", () => {
    // Every message arriving at the window is untrusted; this is the half of
    // that which is about shape rather than origin.
    expect(playheadFrom("not json")).toBeNull();
    expect(playheadFrom(JSON.stringify({ event: "infoDelivery" }))).toBeNull();
    expect(
      playheadFrom(JSON.stringify({ event: "infoDelivery", info: { currentTime: "12" } })),
    ).toBeNull();
    expect(
      playheadFrom(JSON.stringify({ event: "infoDelivery", info: { currentTime: -1 } })),
    ).toBeNull();
    expect(
      playheadFrom(JSON.stringify({ event: "infoDelivery", info: { currentTime: NaN } })),
    ).toBeNull();
    expect(playheadFrom({ event: "infoDelivery" })).toBeNull();
    expect(playheadFrom(null)).toBeNull();
  });
});

describe("isHandshakeAnswered", () => {
  it("recognises every answer the embed gives", () => {
    // Repeating the handshake after it has been answered produces nothing but
    // `alreadyInitialized`, so all three mean "stop asking".
    expect(isHandshakeAnswered(READY)).toBe(true);
    expect(isHandshakeAnswered(INITIAL)).toBe(true);
    expect(isHandshakeAnswered(ALREADY)).toBe(true);
  });

  it("keeps asking until one arrives", () => {
    expect(isHandshakeAnswered(PLAYING)).toBe(false);
    expect(isHandshakeAnswered("not json")).toBe(false);
  });
});
