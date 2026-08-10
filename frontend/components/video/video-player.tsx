"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import type { VideoPlayback } from "@/lib/video-types";

export interface VideoPlayerHandle {
  /** Seek without starting playback; the reader decides when to play. */
  seekTo(milliseconds: number): void;
  /** Fill the screen with the picture, leaving the conversation behind. */
  enterFullscreen(): void;
  /** Whether there is anything to show full screen at all. */
  canFullscreen(): boolean;
}

/** A citation lands slightly before its evidence so the moment is not missed. */
export const CITATION_LEAD_MS = 3_000;

interface VideoPlayerProps {
  playback: VideoPlayback;
  title: string;
  /** Refresh the signed source after a genuine playback failure. */
  onPlaybackError?: () => void | Promise<void>;
}

/** A rotating auth token is not a different piece of media. */
export function mediaIdentity(url: string | null): string | null {
  return url?.split(/[?#]/, 1)[0] ?? null;
}

/**
 * The YouTube embed when the lecture came from YouTube, the local file
 * otherwise. Seeking uses the embed's postMessage command API rather than
 * loading the IFrame Player script, which keeps this component free of an
 * external runtime dependency.
 *
 * Fullscreen is requested on the frame itself rather than on a wrapper, so the
 * picture fills the screen instead of a letterboxed box inside a black page.
 */
export const VideoPlayer = forwardRef<VideoPlayerHandle, VideoPlayerProps>(
  function VideoPlayer({ playback, title, onPlaybackError }, ref) {
    const frameRef = useRef<HTMLIFrameElement>(null);
    const elementRef = useRef<HTMLVideoElement>(null);
    const [mediaSource, setMediaSource] = useState(playback.media_url);
    const refreshAfterErrorRef = useRef(false);

    useEffect(() => {
      const changedMedia =
        playback.media_url !== null &&
        mediaIdentity(playback.media_url) !== mediaIdentity(mediaSource);
      const refreshedFailedUrl =
        refreshAfterErrorRef.current &&
        playback.media_url !== null &&
        playback.media_url !== mediaSource;
      if (
        changedMedia ||
        refreshedFailedUrl ||
        (!mediaSource && playback.media_url)
      ) {
        refreshAfterErrorRef.current = false;
        setMediaSource(playback.media_url);
      }
    }, [playback.media_url, mediaSource]);

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
      enterFullscreen() {
        const target = elementRef.current ?? frameRef.current;
        // Not every browser resolves the promise form, and a rejection here
        // (a permissions policy, an unattached element) must not surface as an
        // unhandled rejection in the reader's console.
        target?.requestFullscreen?.().catch(() => undefined);
      },
      canFullscreen() {
        return Boolean(elementRef.current ?? frameRef.current);
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

    if (mediaSource) {
      return (
        <video
          ref={elementRef}
          className="aspect-video w-full rounded-lg border border-border bg-black"
          src={mediaSource}
          controls
          preload="metadata"
          onError={() => {
            // Polling can return a newly signed URL every expiry bucket. Keep
            // the current URL (and currentTime) while it works; accept a fresh
            // token only after the element itself reports a failure.
            refreshAfterErrorRef.current = true;
            if (playback.media_url && playback.media_url !== mediaSource) {
              refreshAfterErrorRef.current = false;
              setMediaSource(playback.media_url);
            }
            void onPlaybackError?.();
          }}
        >
          <track kind="captions" />
        </video>
      );
    }

    // No 16:9 placeholder: an empty frame pushed the conversation off the
    // screen for exactly the videos that had nothing to show in it.
    return (
      <p className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
        Playback becomes available once the source finishes uploading.
      </p>
    );
  },
);
