"use client";

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

import {
  YOUTUBE_EMBED_ORIGIN,
  isHandshakeAnswered,
  listeningMessage,
  playheadFrom,
} from "@/lib/youtube-playhead";
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
  /**
   * Where the picture is now, in milliseconds.
   *
   * Watching a lecture and asking about it needs this: "here" is a timestamp,
   * and until the player reported one there was nothing for a question to
   * anchor to. Both sources report it — the local element natively, the
   * YouTube embed through the handshake its command API already implies.
   */
  onTimeUpdate?: (milliseconds: number) => void;
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
  function VideoPlayer({ playback, title, onPlaybackError, onTimeUpdate }, ref) {
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

    // The callback is read from a ref so that a caller re-creating it on every
    // render does not tear the listener down and re-run the handshake, which
    // would leave the embed answering `alreadyInitialized` forever.
    const onTimeUpdateRef = useRef(onTimeUpdate);
    useEffect(() => {
      onTimeUpdateRef.current = onTimeUpdate;
    }, [onTimeUpdate]);

    useEffect(() => {
      if (playback.kind !== "youtube" || !playback.youtube_video_id) return;
      const frame = frameRef.current;
      if (!frame) return;

      const ask = () =>
        frame.contentWindow?.postMessage(listeningMessage(), YOUTUBE_EMBED_ORIGIN);
      // Repeated until answered: a handshake sent before the player exists is
      // simply lost, and the frame's `load` event is not a guarantee that its
      // own script is ready to hear one.
      let handshake: number | null = window.setInterval(ask, 800);
      const settle = () => {
        if (handshake === null) return;
        window.clearInterval(handshake);
        handshake = null;
      };

      const onMessage = (event: MessageEvent) => {
        // Every message arriving at the window is untrusted: anything on the
        // page can post to it, and the frame's origin plus its own window are
        // the only things that say this came from the player.
        if (event.origin !== YOUTUBE_EMBED_ORIGIN) return;
        if (event.source !== frame.contentWindow) return;
        if (isHandshakeAnswered(event.data)) settle();
        const milliseconds = playheadFrom(event.data);
        if (milliseconds !== null) onTimeUpdateRef.current?.(milliseconds);
      };

      window.addEventListener("message", onMessage);
      frame.addEventListener("load", ask);
      ask();
      return () => {
        settle();
        window.removeEventListener("message", onMessage);
        frame.removeEventListener("load", ask);
      };
    }, [playback.kind, playback.youtube_video_id]);

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
          onTimeUpdate={(event) =>
            onTimeUpdate?.(Math.round(event.currentTarget.currentTime * 1000))
          }
          // Seeking while paused moves the playhead without playing, and a
          // reader who scrubs to a moment to ask about it has not started it.
          onSeeked={(event) =>
            onTimeUpdate?.(Math.round(event.currentTarget.currentTime * 1000))
          }
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
