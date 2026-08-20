import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { VideoPlayer } from "@/components/video/video-player";
import { YOUTUBE_EMBED_ORIGIN } from "@/lib/youtube-playhead";
import type { VideoPlayback } from "@/lib/video-types";

const YOUTUBE: VideoPlayback = {
  kind: "youtube",
  youtube_video_id: "aircAruvnKk",
  media_url: null,
};

function deliver(frame: HTMLIFrameElement, data: string, origin = YOUTUBE_EMBED_ORIGIN) {
  window.dispatchEvent(
    new MessageEvent("message", {
      data,
      origin,
      source: frame.contentWindow,
    }),
  );
}

const PLAYING = JSON.stringify({
  event: "infoDelivery",
  info: { currentTime: 724.04, playerState: 1 },
});

describe("VideoPlayer playhead reporting", () => {
  it("asks the embed to report, and passes on what it says", async () => {
    const onTimeUpdate = vi.fn();
    const { container } = render(
      <VideoPlayer
        playback={YOUTUBE}
        title="Lecture 1"
        onTimeUpdate={onTimeUpdate}
      />,
    );
    const frame = container.querySelector("iframe")!;

    await waitFor(() => expect(frame.contentWindow).toBeTruthy());
    deliver(frame, PLAYING);

    expect(onTimeUpdate).toHaveBeenCalledWith(724040);
  });

  it("ignores a message from anywhere but the embed", () => {
    // Anything on the page can post to the window. The frame's origin and its
    // own window are the only things that say a message came from the player.
    const onTimeUpdate = vi.fn();
    const { container } = render(
      <VideoPlayer
        playback={YOUTUBE}
        title="Lecture 1"
        onTimeUpdate={onTimeUpdate}
      />,
    );
    const frame = container.querySelector("iframe")!;

    deliver(frame, PLAYING, "https://evil.example");
    window.dispatchEvent(
      new MessageEvent("message", {
        data: PLAYING,
        origin: YOUTUBE_EMBED_ORIGIN,
        source: window,
      }),
    );

    expect(onTimeUpdate).not.toHaveBeenCalled();
  });

  it("stops asking once the embed has answered", async () => {
    vi.useFakeTimers();
    try {
      const { container } = render(
        <VideoPlayer playback={YOUTUBE} title="Lecture 1" onTimeUpdate={vi.fn()} />,
      );
      const frame = container.querySelector("iframe")!;
      const post = vi.spyOn(frame.contentWindow!, "postMessage");

      vi.advanceTimersByTime(1_700);
      const asksBefore = post.mock.calls.length;
      expect(asksBefore).toBeGreaterThan(0);

      deliver(frame, JSON.stringify({ event: "onReady" }));
      vi.advanceTimersByTime(4_000);

      // Repeating the handshake after it is answered yields only
      // `alreadyInitialized`, which is noise.
      expect(post.mock.calls.length).toBe(asksBefore);
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports the local element's own time", () => {
    const onTimeUpdate = vi.fn();
    const { container } = render(
      <VideoPlayer
        playback={{ kind: "local", youtube_video_id: null, media_url: "blob:x" }}
        title="Lecture 1"
        onTimeUpdate={onTimeUpdate}
      />,
    );
    const element = container.querySelector("video")!;
    Object.defineProperty(element, "currentTime", { value: 12.5, writable: true });

    element.dispatchEvent(new Event("timeupdate"));

    expect(onTimeUpdate).toHaveBeenCalledWith(12500);
  });
});
