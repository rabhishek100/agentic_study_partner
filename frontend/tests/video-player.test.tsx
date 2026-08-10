import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  VideoPlayer,
  mediaIdentity,
} from "@/components/video/video-player";
import type { VideoPlayback } from "@/lib/video-types";

function local(url: string): VideoPlayback {
  return { kind: "local", youtube_video_id: null, media_url: url };
}

describe("VideoPlayer", () => {
  it("treats rotating signed tokens as the same media", () => {
    expect(mediaIdentity("/api/videos/v1/stream?token=first")).toBe(
      "/api/videos/v1/stream",
    );
    expect(mediaIdentity("/api/videos/v1/stream?token=second")).toBe(
      "/api/videos/v1/stream",
    );
  });

  it("preserves the element, source, and position when a token rotates", () => {
    const { container, rerender } = render(
      <VideoPlayer
        playback={local("/api/videos/v1/stream?token=first")}
        title="Lecture"
      />,
    );
    const video = container.querySelector("video") as HTMLVideoElement;
    video.currentTime = 42;

    rerender(
      <VideoPlayer
        playback={local("/api/videos/v1/stream?token=second")}
        title="Lecture"
      />,
    );

    expect(container.querySelector("video")).toBe(video);
    expect(video.getAttribute("src")).toContain("token=first");
    expect(video.currentTime).toBe(42);
  });

  it("accepts the latest token after the current source actually fails", async () => {
    const onPlaybackError = vi.fn();
    const { container, rerender } = render(
      <VideoPlayer
        playback={local("/api/videos/v1/stream?token=first")}
        title="Lecture"
        onPlaybackError={onPlaybackError}
      />,
    );
    const video = container.querySelector("video") as HTMLVideoElement;
    rerender(
      <VideoPlayer
        playback={local("/api/videos/v1/stream?token=second")}
        title="Lecture"
        onPlaybackError={onPlaybackError}
      />,
    );

    fireEvent.error(video);

    await waitFor(() =>
      expect(video.getAttribute("src")).toContain("token=second"),
    );
    expect(onPlaybackError).toHaveBeenCalledOnce();
  });
});
