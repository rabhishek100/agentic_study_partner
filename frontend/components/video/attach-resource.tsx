"use client";

import { AlertCircle, Loader2, Paperclip } from "lucide-react";
import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch, uploadUrl } from "@/lib/api";
import { accessToken } from "@/lib/supabase";
import type { VideoResource } from "@/lib/video-types";

interface Reservation {
  resource: VideoResource;
  upload_url: string;
}

/**
 * Attach slides or notes to a lecture that already exists.
 *
 * Documents used to be collectable only while creating the video, so a deck
 * that failed to arrive — or one found afterwards — left the reader with a
 * resource panel they could read but not change.
 */
export function AttachResource({
  videoId,
  onAttached,
}: {
  videoId: string;
  onAttached(): void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function attach() {
    if (busy || (!file && !url.trim())) return;
    setBusy(true);
    setError("");
    try {
      if (file) {
        const reservation = await apiFetch<Reservation>(
          `/videos/${videoId}/resources/uploads`,
          {
            method: "POST",
            body: JSON.stringify({
              original_filename: file.name,
              content_length: file.size,
              title: file.name.replace(/\.pdf$/i, ""),
              role: "slides",
              required: false,
            }),
          },
        );
        const token = await accessToken();
        const response = await fetch(uploadUrl(reservation.upload_url), {
          method: "PUT",
          headers: {
            "Content-Type": "application/pdf",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          body: file,
        });
        if (!response.ok) {
          throw new Error(`Upload failed (${response.status})`);
        }
      } else {
        await apiFetch(`/videos/${videoId}/resources/links`, {
          method: "POST",
          body: JSON.stringify({
            resource_kind: "pdf",
            title: "Slides",
            url: url.trim(),
            role: "slides",
            required: false,
          }),
        });
      }
      setFile(null);
      setUrl("");
      onAttached();
    } catch (caught) {
      setError((caught as Error).message || "Could not attach that document.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-dashed border-border p-3">
      <div className="space-y-1.5">
        <Label htmlFor="attach-file">Attach slides or notes (PDF)</Label>
        <Input
          id="attach-file"
          type="file"
          accept="application/pdf"
          disabled={busy || url.trim().length > 0}
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="attach-url">…or a PDF link</Label>
        <Input
          id="attach-url"
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://…/slides.pdf"
          disabled={busy || file !== null}
        />
      </div>
      {error ? (
        <Alert variant="destructive">
          <AlertCircle aria-hidden />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      ) : null}
      <Button
        size="sm"
        onClick={attach}
        disabled={busy || (!file && !url.trim())}
      >
        {busy ? (
          <Loader2 aria-hidden className="animate-spin" />
        ) : (
          <Paperclip aria-hidden />
        )}
        Attach
      </Button>
      <p className="text-xs text-muted-foreground">
        A document attached now is read on the next rebuild, which reuses the
        transcript, frames, and visual analysis this lecture already paid for.
      </p>
    </div>
  );
}
