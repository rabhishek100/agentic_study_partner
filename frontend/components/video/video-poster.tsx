"use client";

import { useState } from "react";

import { useAuthenticatedImage } from "@/hooks/use-authenticated-image";
import { formatTimestamp, type VideoSummary } from "@/lib/video-types";
import { cn } from "@/lib/utils";

/**
 * YouTube's own thumbnail for a lecture it hosts.
 *
 * `mqdefault` is 320×180 and exists for every video, including ones with no
 * uploaded custom art — `maxresdefault` does not, and a missing one returns a
 * 120×90 grey placeholder rather than a 404, so it cannot be detected and
 * falls back to nothing.
 */
function youtubePoster(video: VideoSummary): string | null {
  const id = video.playback.youtube_video_id;
  return id ? `https://i.ytimg.com/vi/${id}/mqdefault.jpg` : null;
}

/**
 * The lecture's initials, for a poster there is no image for.
 *
 * Uploads carry filenames rather than titles — `cme295-lecture1-h264.mp4` —
 * so the separators that filenames use are word breaks here, and the codec
 * and container noise at the end is dropped by taking the first two words.
 */
export function monogram(title: string): string {
  const words = title
    .replace(/\.[a-z0-9]{2,4}$/i, "")
    .split(/[\s._\-–—:]+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter(Boolean);
  const letters = words.slice(0, 2).map((word) => word[0] ?? "");
  return letters.join("").toUpperCase() || "—";
}

/**
 * A lecture's face.
 *
 * Video is the one medium where the image is the identity, and the list has
 * been showing filenames. YouTube lectures use YouTube's thumbnail; everything
 * else gets a monogram tile, which is a designed state rather than a gap — it
 * never renders as a broken image, and it upgrades on its own the day the
 * summary starts carrying a poster frame.
 */
export function VideoPoster({
  video,
  className,
}: {
  video: VideoSummary;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const youtube = youtubePoster(video);
  /*
    An uploaded lecture's poster is one of its own stored frames, served by an
    endpoint that authenticates by bearer token — which an `<img src>` cannot
    carry — so the bytes come through the same authenticated-image path the
    workspace's timeline uses. The hook is called unconditionally and given an
    empty source when there is nothing to fetch, because hooks cannot be
    called behind a condition.
  */
  const frame = useAuthenticatedImage(
    !youtube && video.poster_frame_id !== null
      ? `/api/videos/${video.video_id}/frames/${video.poster_frame_id}/image`
      : "",
  );
  const source = youtube ?? (frame.status === "ready" ? frame.url : null);
  const duration = video.duration_ms ? formatTimestamp(video.duration_ms) : null;

  return (
    <div
      className={cn(
        "relative aspect-video overflow-hidden rounded-lg border border-divider bg-surface",
        className,
      )}
    >
      {/*
        The monogram is the ground, not the fallback branch. Layering the
        thumbnail over it means a slow or blocked image shows initials the
        whole time rather than an empty rectangle that resolves late — and it
        does not depend on `onError` firing, which a request that merely hangs
        never does.
      */}
      <span
        aria-hidden
        className="grid size-full place-items-center font-serif text-2xl font-medium text-muted-foreground"
      >
        {monogram(video.title)}
      </span>
      {source && !failed ? (
        <img
          src={source}
          alt=""
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover"
          onError={() => setFailed(true)}
        />
      ) : null}
      {duration ? (
        /*
          On the poster, not in the meta line: duration is the one fact a
          reader weighs before opening a lecture, and it belongs where every
          other video interface has trained them to look for it.
        */
        <span className="absolute bottom-1 right-1 rounded-sm bg-canvas px-1 font-mono text-xs tabular-nums text-foreground">
          {duration}
        </span>
      ) : null}
    </div>
  );
}
