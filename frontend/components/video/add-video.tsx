"use client";

import { trackedFetch } from "@/lib/analytics";

import { Loader2, Plus } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, uploadUrl } from "@/lib/api";
import { accessToken } from "@/lib/supabase";
import type { VideoResource } from "@/lib/video-types";

interface CreatedVideo {
  video_id: string;
  ingestion_job_id: string;
  upload?: { upload_url: string; maximum_bytes: number } | null;
}

interface ResourceReservation {
  resource: VideoResource;
  upload_url: string;
}

async function putBytes(url: string, file: File, contentType: string) {
  const token = await accessToken();
  const response = await trackedFetch(uploadUrl(url), {
    method: "PUT",
    headers: {
      "Content-Type": contentType,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: file,
  });
  if (!response.ok) {
    throw new Error(`Upload failed (${response.status})`);
  }
}

/**
 * Create a lecture and attach its slides in one pass.
 *
 * Slides are collected here rather than after ingestion on purpose: the
 * pipeline reads linked documents as one of its stages, so attaching them up
 * front means the first published version can already cite the deck.
 */
export function AddVideo({ onAdded }: { onAdded(): void }) {
  const [url, setUrl] = useState("");
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [captionFile, setCaptionFile] = useState<File | null>(null);
  const [slidesUrl, setSlidesUrl] = useState("");
  const [slidesFile, setSlidesFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function attachCaptions(videoId: string) {
    if (!captionFile) return;
    const token = await accessToken();
    const response = await trackedFetch(uploadUrl(`/videos/${videoId}/captions`), {
      method: "PUT",
      headers: {
        "Content-Type": "text/vtt",
        "X-Caption-Filename": captionFile.name,
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: captionFile,
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`Captions were rejected: ${detail.slice(0, 200)}`);
    }
  }

  async function attachSlides(videoId: string) {
    if (slidesFile) {
      const reservation = await apiFetch<ResourceReservation>(
        `/videos/${videoId}/resources/uploads`,
        {
          method: "POST",
          body: JSON.stringify({
            original_filename: slidesFile.name,
            content_length: slidesFile.size,
            title: slidesFile.name.replace(/\.pdf$/i, ""),
            role: "slides",
            required: false,
          }),
        },
      );
      await putBytes(reservation.upload_url, slidesFile, "application/pdf");
      return;
    }
    if (slidesUrl.trim()) {
      await apiFetch(`/videos/${videoId}/resources/links`, {
        method: "POST",
        body: JSON.stringify({
          resource_kind: "pdf",
          title: "Slides",
          url: slidesUrl.trim(),
          role: "slides",
          required: false,
        }),
      });
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    setBusy(true);
    try {
      let created: CreatedVideo;
      let attached = false;
      if (videoFile) {
        created = await apiFetch<CreatedVideo>("/videos/uploads", {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({
            original_filename: videoFile.name,
            content_type: videoFile.type || "video/mp4",
            content_length: videoFile.size,
          }),
        });
        // Captions and slides go first, while the job still waits for bytes.
        // The video landing starts the worker, and it reaches the transcript
        // and resource stages within seconds — anything attached after that
        // is simply not there when those stages look for it.
        await attachCaptions(created.video_id);
        await attachSlides(created.video_id);
        attached = true;
        if (created.upload) {
          await putBytes(
            created.upload.upload_url,
            videoFile,
            videoFile.type || "video/mp4",
          );
        }
      } else {
        created = await apiFetch<CreatedVideo>("/videos/youtube", {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ url: url.trim() }),
        });
      }
      if (!attached) await attachSlides(created.video_id);
      setUrl("");
      setSlidesUrl("");
      setVideoFile(null);
      setCaptionFile(null);
      setSlidesFile(null);
      onAdded();
    } catch (caught) {
      setError((caught as Error).message || "Could not add that video.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div className="space-y-2">
        <Label htmlFor="video-url">YouTube URL</Label>
        <Input
          id="video-url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://www.youtube.com/watch?v=…"
          disabled={busy || videoFile !== null}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="video-file">…or upload a video file</Label>
        <Input
          id="video-file"
          type="file"
          accept="video/mp4,video/webm,video/quicktime"
          disabled={busy || url.trim().length > 0}
          onChange={(event) => setVideoFile(event.target.files?.[0] ?? null)}
        />
      </div>
      {videoFile ? (
        <div className="space-y-2">
          <Label htmlFor="caption-file">Captions .vtt (recommended)</Label>
          <Input
            id="caption-file"
            type="file"
            accept=".vtt,text/vtt"
            disabled={busy}
            onChange={(event) => setCaptionFile(event.target.files?.[0] ?? null)}
          />
          <p className="text-xs text-muted-foreground">
            Without captions the lecture is transcribed by a paid model, which
            for a long recording can exceed the per-video cost cap.
          </p>
        </div>
      ) : null}
      <div className="space-y-2">
        <Label htmlFor="slides-url">Slides PDF URL (optional)</Label>
        <Input
          id="slides-url"
          value={slidesUrl}
          onChange={(event) => setSlidesUrl(event.target.value)}
          placeholder="https://…/slides.pdf"
          disabled={busy || slidesFile !== null}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="slides-file">…or upload the slides (optional)</Label>
        <Input
          id="slides-file"
          type="file"
          accept="application/pdf"
          disabled={busy || slidesUrl.trim().length > 0}
          onChange={(event) => setSlidesFile(event.target.files?.[0] ?? null)}
        />
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Button
        type="submit"
        className="w-full"
        disabled={busy || (!url.trim() && !videoFile)}
      >
        {busy ? (
          <Loader2 aria-hidden className="animate-spin" />
        ) : (
          <Plus aria-hidden />
        )}
        Add lecture
      </Button>
    </form>
  );
}
