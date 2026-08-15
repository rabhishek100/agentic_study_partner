"use client";

import {
  Captions,
  FileText,
  Film,
  Link2,
  Loader2,
  MonitorPlay,
  Plus,
  Upload,
} from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, uploadUrl } from "@/lib/api";
import { accessToken } from "@/lib/supabase";
import type { VideoResource } from "@/lib/video-types";
import { cn } from "@/lib/utils";

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
  const response = await fetch(uploadUrl(url), {
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
  const [sourceMode, setSourceMode] = useState<"youtube" | "upload">("youtube");
  const [slidesMode, setSlidesMode] = useState<"none" | "url" | "file">("none");
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
    const response = await fetch(uploadUrl(`/videos/${videoId}/captions`), {
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
      setSlidesMode("none");
      onAdded();
    } catch (caught) {
      setError((caught as Error).message || "Could not add that video.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      <div className="grid divide-y divide-border lg:grid-cols-[1.15fr_0.8fr_1fr] lg:divide-x lg:divide-y-0">
        <fieldset className="space-y-4 pb-5 lg:pb-0 lg:pr-6">
          <legend className="flex items-center gap-2 text-sm font-medium">
            <span className="font-mono text-xs text-primary">1</span>
            Add video
          </legend>
          <div className="grid grid-cols-2 gap-2">
            <Button
              type="button"
              variant={sourceMode === "youtube" ? "secondary" : "outline"}
              className="justify-start"
              aria-pressed={sourceMode === "youtube"}
              onClick={() => {
                setSourceMode("youtube");
                setVideoFile(null);
                setCaptionFile(null);
              }}
              disabled={busy}
            >
              <Link2 aria-hidden />
              YouTube
            </Button>
            <Button
              type="button"
              variant={sourceMode === "upload" ? "secondary" : "outline"}
              className="justify-start"
              aria-pressed={sourceMode === "upload"}
              onClick={() => {
                setSourceMode("upload");
                setUrl("");
              }}
              disabled={busy}
            >
              <Film aria-hidden />
              Video file
            </Button>
          </div>

          {sourceMode === "youtube" ? (
            <div className="space-y-1.5">
              <Label htmlFor="video-url">YouTube URL</Label>
              <Input
                id="video-url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://www.youtube.com/watch?v=…"
                disabled={busy}
              />
            </div>
          ) : (
            <div className="space-y-3">
              <input
                id="video-file"
                type="file"
                accept="video/mp4,video/webm,video/quicktime"
                className="sr-only"
                disabled={busy}
                onChange={(event) =>
                  setVideoFile(event.target.files?.[0] ?? null)
                }
              />
              <Label
                htmlFor="video-file"
                className={cn(
                  "flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-input bg-background/40 p-4 text-center transition-colors hover:border-primary hover:bg-accent/40",
                  busy && "pointer-events-none opacity-60",
                )}
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  if (!busy) setVideoFile(event.dataTransfer.files[0] ?? null);
                }}
              >
                <Upload aria-hidden className="size-5 text-primary" />
                <span className="text-sm font-medium">
                  {videoFile?.name ?? "Choose or drop a video"}
                </span>
                <span className="text-xs font-normal text-muted-foreground">
                  MP4, WebM, or QuickTime
                </span>
              </Label>

              <input
                id="caption-file"
                type="file"
                accept=".vtt,text/vtt"
                className="sr-only"
                disabled={busy}
                onChange={(event) =>
                  setCaptionFile(event.target.files?.[0] ?? null)
                }
              />
              <Label
                htmlFor="caption-file"
                className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground hover:text-foreground"
              >
                <Captions aria-hidden className="size-4" />
                {captionFile?.name ?? "Attach captions (.vtt) — recommended"}
              </Label>
            </div>
          )}
        </fieldset>

        <section className="space-y-4 py-5 lg:px-6 lg:py-0" aria-labelledby="capture-step">
          <h3 id="capture-step" className="flex items-center gap-2 text-sm font-medium">
            <span className="font-mono text-xs text-primary">2</span>
            Capture screen
          </h3>
          <div className="flex items-start gap-3">
            <MonitorPlay aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
            <p className="text-xs leading-relaxed text-muted-foreground">
              Mugensei automatically extracts what was shown and aligns it with
              the transcript for visual evidence.
            </p>
          </div>
        </section>

        <fieldset className="space-y-4 pt-5 lg:pl-6 lg:pt-0">
          <legend className="flex items-center gap-2 text-sm font-medium">
            <span className="font-mono text-xs text-seal">3</span>
            Add slides <span className="font-normal text-muted-foreground">(optional)</span>
          </legend>
          <div className="flex flex-wrap gap-1.5">
            {(["none", "url", "file"] as const).map((mode) => (
              <Button
                key={mode}
                type="button"
                size="xs"
                variant={slidesMode === mode ? "secondary" : "ghost"}
                aria-pressed={slidesMode === mode}
                onClick={() => {
                  setSlidesMode(mode);
                  if (mode !== "url") setSlidesUrl("");
                  if (mode !== "file") setSlidesFile(null);
                }}
                disabled={busy}
              >
                {mode === "none" ? "Not now" : mode === "url" ? "PDF link" : "PDF file"}
              </Button>
            ))}
          </div>
          {slidesMode === "url" ? (
            <div className="space-y-1.5">
              <Label htmlFor="slides-url">Slides PDF URL</Label>
              <Input
                id="slides-url"
                value={slidesUrl}
                onChange={(event) => setSlidesUrl(event.target.value)}
                placeholder="https://…/slides.pdf"
                disabled={busy}
              />
            </div>
          ) : slidesMode === "file" ? (
            <>
              <input
                id="slides-file"
                type="file"
                accept="application/pdf"
                className="sr-only"
                disabled={busy}
                onChange={(event) =>
                  setSlidesFile(event.target.files?.[0] ?? null)
                }
              />
              <Label
                htmlFor="slides-file"
                className="flex min-h-24 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-input p-3 text-center hover:border-primary hover:bg-accent/40"
                onDragOver={(event) => event.preventDefault()}
                onDrop={(event) => {
                  event.preventDefault();
                  if (!busy) setSlidesFile(event.dataTransfer.files[0] ?? null);
                }}
              >
                <FileText aria-hidden className="size-5 text-seal" />
                <span className="text-xs font-medium">
                  {slidesFile?.name ?? "Choose or drop a PDF"}
                </span>
              </Label>
            </>
          ) : (
            <p className="text-xs leading-relaxed text-muted-foreground">
              You can attach or replace slides after the lecture is added.
            </p>
          )}
        </fieldset>
      </div>

      {error ? (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Button
        type="submit"
        className="ml-auto flex w-full sm:w-auto"
        disabled={busy || (sourceMode === "youtube" ? !url.trim() : !videoFile)}
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
