"use client";

import { forwardRef, useImperativeHandle, useRef } from "react";

import type { VideoPlayback } from "@/lib/video-types";

export interface VideoPlayerHandle {
  /** Seek without starting playback; the reader decides when to play. */
  seekTo(milliseconds: number): void;
}

/** A citation lands slightly before its evidence so the moment is not missed. */
export const CITATION_LEAD_MS = 3_000;

interface VideoPlayerProps {
  playback: VideoPlayback;
  title: string;
}

/**
 * The YouTube embed when the lecture came from YouTube, the local file
 * otherwise. Seeking uses the embed's postMessage command API rather than
 * loading the IFrame Player script, which keeps this component free of an
 * external runtime dependency.
 */
export const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(
  function VideoPlayer({ playback, title }, ref) {
    const frameRef = useRef<HTMLIFrameElement>(null);
    const elementRef = useRef<HTMLVideoElement>(null);

    useImperativeHandle(ref, () => ({
      seekTo(milliseconds: number) {
        const seconds = Math.max(0, (milliseconds - CITATION_LEAD_MS) / 1000);
        if (elementRef.current) {
          elementRef.current.currentTime = seconds;
          return;
        }
        frameRef.current?.contentWindow?.postMessage(
          JSON.stringify({
            event: "command",
            func: "seekTo",
            args: [seconds, true],
          }),
          "https://www.youtube-nocookie.com",
        );
      },
    }));

    if (playback.kind === "youtube" && playback.youtube_video_id) {
      return (
        <div className="aspect-video w-full overflow-hidden rounded-lg border border-border bg-black">
          <iframe
            ref={frameRef}
            className="size-full"
            src={`https://www.youtube-nocookie.com/embed/${playback.youtube_video_id}?enablejsapi=1&rel=0`}
            title={title}
            allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
          />
        </div>
      );
    }

    if (playback.media_url) {
      return (
        <video
          ref={elementRef}
          className="aspect-video w-full rounded-lg border border-border bg-black"
          src={playback.media_url}
          controls
          preload="metadata"
        >
          <track kind="captions" />
        </video>
      );
    }

    return (
      <div className="grid aspect-video w-full place-items-center rounded-lg border border-dashed border-border bg-muted/40 text-sm text-muted-foreground">
        Playback becomes available once the source finishes uploading.
      </div>
    );
  },
);
